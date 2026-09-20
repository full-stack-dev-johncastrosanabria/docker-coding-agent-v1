"""Evaluate G4 and write gates/G4.json (tasks.md T015): effective strict network policy.

Standard library only. G4 proves, for every {claude, codex} x {trusted, untrusted} cell, that the
**effective** per-sandbox policy permits exactly the destinations that profile requires and denies
everything else, on four surfaces that must agree:

  1. the **effective rule set** itself (`sbx policy ls <sandbox> --json`) - every active network rule
     applicable to the sandbox is inspected and must be accounted for;
  2. `sbx policy check network --sandbox <name> <dest> --json` - the authorizer's decision;
  3. a real connection attempt from inside the sandbox - the proxy either establishes the CONNECT
     tunnel or answers it itself;
  4. `sbx policy log <name> --json` - the destination appears under allowed_hosts or blocked_hosts
     with a matching reason.

Surface 1 is not redundant. Surfaces 2-4 only ever speak about destinations G4 thought to probe, so
none of them can detect a **latent** allow rule for a destination no probe exercised. Only reading
the effective rule set and accounting for every rule in it can, which is why a cell fails unless
every active allow is either a destination the profile permits or a kit allow that a matching
explicit sandbox-scoped deny neutralizes.

The matrix is **derived from the committed T014 inventory and trusted-allowlist draft**, not
restated here: required hosts are the entries the inventory marks `sandbox_required`, the trusted
profile's allowlist is every candidate the draft declares, and the must-deny set is everything else
those two documents name. If T014 changes, G4's matrix changes with it, and a draft entry that does
not validate stops the gate rather than being silently approved.

Nothing global is mutated: every rule this gate adds carries `--sandbox`, and the global
network-policy fingerprint is captured with zero sandboxes present, before and after, and must be
identical. It is written to the evidence as the typed `network_policy_fingerprint` field, so T024
propagates it without parsing prose. This gate never calls `sbx policy init`, `sbx reset` or
`sbx rm --all`. If a one-time developer-authorized restoration of the documented post-G0 global
baseline was performed before the run, `gates/G4/prereq/restoration.json` records it and the notes
report it as a prerequisite restoration - never as a G0 proof, and never inherited by a later rerun.

Usage:
  python3 gates/G4/record.py --inputs                      emit the T014 input digests
  python3 gates/G4/record.py --plan                        emit the matrix for run.sh
  python3 gates/G4/record.py --preflight <obs> [<work>]    exit 0 only if the post-G0 state holds
  python3 gates/G4/record.py <obs> [<work>]                write gates/G4.json
"""

import datetime
import hashlib
import importlib.util
import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
WORK = os.path.join(ROOT, "gates", "G4", "work")
VERSIONS = os.path.join(ROOT, "runtime", "versions.yaml")
EVIDENCE = os.path.join(ROOT, "gates", "G4.json")
# A one-time, developer-authorized restoration of the documented post-G0 global baseline,
# recorded when the sandboxd VM had dropped it. Present only when such a restoration was
# performed before this run, so a later rerun never inherits the claim.
PREREQ_FILE = os.path.join(ROOT, "gates", "G4", "prereq", "restoration.json")
INVENTORY_FILE = os.path.join(ROOT, "gates", "inventory", "control-plane-hosts.json")
DRAFT_FILE = os.path.join(ROOT, "gates", "inventory", "trusted-allowlist.draft.json")
INVENTORY_EVIDENCE = os.path.join(ROOT, "gates", "INVENTORY.json")

# The T014 artifacts G4's matrix is derived from. G4 binds its evidence to their exact content, so
# an edit after the reviewed T014 state cannot silently produce a different allow set: the digests
# recorded here stop matching and the gate must be re-run against the revised - and re-reviewed -
# inputs. The host names themselves are never hard-coded; only the identity of the files is.
T014_INPUTS = (
    ("inventory_evidence_sha256", INVENTORY_EVIDENCE),
    ("control_plane_hosts_sha256", INVENTORY_FILE),
    ("trusted_allowlist_draft_sha256", DRAFT_FILE),
)

PROFILE_VALUES = ("trusted", "untrusted")

PROFILES = ("trusted", "untrusted")
AGENT = {"claude": "claude", "codex": "docker-agent"}
PORT = 443

# The R12 categories the trusted allowlist may draw on (plan.md: declared package registries,
# read-only source-control fetch hosts, documentation hosts). A draft candidate in any other
# category stops the gate instead of being approved by default.
TRUSTED_CATEGORIES = ("package-registry", "source-control-fetch", "documentation")

# The single named host granted to an untrusted run, standing in for a host-authoritative per-run
# grant. It must itself be a trusted candidate, and every other candidate must still be denied
# there, which is what proves untrusted never inherits the trusted allowlist.
UNTRUSTED_GRANT = "pypi.org"

# Representative destinations outside the V1 set that a broad preset or a kit would otherwise
# allow. They are must-deny in every cell and must never be trusted candidates:
# raw.githubusercontent.com is deliberately GitHub-adjacent, so a source-control allowance that
# leaked into a wildcard would show up here.
EXTRA_MUST_DENY = ("storage.googleapis.com", "raw.githubusercontent.com")

ALLOW = "allow"
DENY_EXPLICIT = "deny-explicit"
DENY_IMPLICIT = "deny-implicit"

# The rule shape `sbx policy ls <sandbox> --json` emits on the pinned sbx v0.43.0. A document that
# does not carry these fields is shape drift, and G4 fails closed rather than guessing.
EFFECTIVE_RULE_KEYS = (
    "id", "name", "scope", "applies_to", "resource_type", "decision",
    "resources", "origin", "layer", "status", "editable",
)

# Every resource_type the pinned sbx v0.43.0 emitted across this gate's captured policy documents,
# global and per-sandbox alike - nothing else was ever observed, and no broader alias is assumed.
# Rules are selected for accounting by resource_type, which is the one pinned key whose drift the
# shape check below can never catch: a rule discarded by that field produces no failure and no
# evidence, so the gate would assert that every active network rule is accounted for while never
# having looked at it. An unobserved value is therefore reported, not skipped.
OBSERVED_RESOURCE_TYPES = ("filesystem:read", "filesystem:write", "network")
NETWORK_RESOURCE_TYPE = "network"

# Every semantically relevant field of a GLOBAL network rule enters the fingerprint. Narrowing this
# set is what let a change to applies_to or resource_type leave the digest unmoved, so the canonical
# input names the complete rule shape and the evidence-schema description repeats it verbatim.
FINGERPRINT_RULE_KEYS = EFFECTIVE_RULE_KEYS

# `sbx policy check` reports the DECISION in its exit status - 0 allowed, 1 denied - and also exits
# 1 on a usage error, so the exit code carries no success signal and cannot be the fail-closed test
# (under the documented deny-all baseline the healthy probe is a denial, i.e. exit 1). It is
# captured for diagnosis; what must hold is that the document is a decided check for the probe
# target carrying a real boolean governance.active.
GOVERNANCE_PROBE = "example.invalid:443"

HOSTNAME = re.compile(r"^[a-z0-9]([a-z0-9-]*[a-z0-9])?(\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)+$")

PREFLIGHT = ("G4.0a", "G4.0b", "G4.0c", "G4.0d")


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


preflight_module = _load("dca_preflight", os.path.join(ROOT, "gates", "preflight.py"))
rules_module = _load("eligibility_rules", os.path.join(ROOT, "gates", "eligibility_rules.py"))


def read_observations(path):
    observations = {}
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            key, sep, value = line.rstrip("\n").partition("=")
            if sep:
                observations[key] = value
    return observations


def read_json(directory, name):
    try:
        with open(os.path.join(directory, name), encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return None


def read_file(directory, name):
    try:
        with open(os.path.join(directory, name), encoding="utf-8") as fh:
            return fh.read()
    except OSError:
        return None


def file_digest(path):
    """'sha256:' + the digest of a file's exact bytes, or None when it cannot be read.

    Same convention as runtime_versions_digest and the evidence contract's #/$defs/digest, so the
    T014 binding reuses the mechanism the project already has rather than a parallel one.
    """
    try:
        with open(path, "rb") as fh:
            return "sha256:" + hashlib.sha256(fh.read()).hexdigest()
    except OSError:
        return None


def input_digests():
    """The current identity of every T014 artifact G4 derives its matrix from."""
    return {name: file_digest(path) for name, path in T014_INPUTS}


def host_port_of(resource):
    """(host, port) for a policy resource; port is None when the resource names none.

    The pinned sbx emits both shapes: the built-in kit's rule carries `api.anthropic.com:443`
    while a rule created with `sbx policy allow network --sandbox <name> <host>` carries the bare
    host. The port is kept rather than discarded because it is part of what a rule grants.
    """
    text = str(resource)
    head, sep, tail = text.rpartition(":")
    if sep and tail.isdigit():
        return head, int(tail)
    return text, None


def host_of(resource):
    """The bare host a policy resource names, dropping the optional :port suffix."""
    return host_port_of(resource)[0]


# --- the trusted allowlist, derived from the committed T014 draft --------------------------------

def trusted_candidates(draft):
    """Every host the T014 trusted-allowlist draft declares, in order.

    The relation to the draft is explicit rather than a hard-coded subset, so G4 exercises the
    architecture R12 and plan.md actually specify. It is also validated rather than trusted: an
    entry in an undeclared category, an approved one, a malformed one or one claiming to be
    sandbox-required raises, so a future draft edit cannot quietly widen what G4 proves.
    """
    if not isinstance(draft, dict):
        raise ValueError("the trusted-allowlist draft is not an object")
    if draft.get("status") != "draft" or draft.get("approved") is not False:
        raise ValueError("the trusted-allowlist draft must still be an unapproved draft")
    if draft.get("applies_to_profiles") != ["trusted"]:
        raise ValueError(
            f"the draft applies to {draft.get('applies_to_profiles')!r}; the trusted allowlist "
            "applies to the trusted profile only and must never widen untrusted access"
        )
    declared = draft.get("categories")
    if not isinstance(declared, dict) or not declared:
        raise ValueError("the draft declares no R12 categories")
    unknown = sorted(set(declared) - set(TRUSTED_CATEGORIES))
    if unknown:
        raise ValueError(f"the draft declares categories outside R12: {unknown}")

    candidates = draft.get("candidates")
    if not isinstance(candidates, list) or not candidates:
        raise ValueError("the draft declares no trusted candidates")
    hosts = []
    for index, entry in enumerate(candidates):
        where = f"candidates[{index}]"
        if not isinstance(entry, dict):
            raise ValueError(f"{where} is not an object")
        host = entry.get("host")
        if not isinstance(host, str) or not HOSTNAME.match(host):
            raise ValueError(f"{where} host {host!r} is not a bare lowercase hostname")
        if host in hosts:
            raise ValueError(f"the draft lists {host} twice")
        if entry.get("port") != PORT:
            raise ValueError(f"{where} ({host}) port {entry.get('port')!r} is not {PORT}")
        if entry.get("category") not in declared:
            raise ValueError(f"{where} ({host}) category {entry.get('category')!r} is not declared")
        if entry.get("approved") is not False:
            raise ValueError(f"{where} ({host}) must still be an unapproved candidate")
        if "sandbox_required" in entry:
            raise ValueError(f"{where} ({host}) claims sandbox_required; it is a profile allowance")
        if not isinstance(entry.get("evidence_ref"), str) or not entry["evidence_ref"].strip():
            raise ValueError(f"{where} ({host}) has no usable evidence_ref")
        hosts.append(host)

    if UNTRUSTED_GRANT not in hosts:
        raise ValueError(f"the untrusted grant {UNTRUSTED_GRANT} is not a trusted candidate")
    overlap = sorted(set(EXTRA_MUST_DENY) & set(hosts))
    if overlap:
        raise ValueError(f"must-deny control hosts appear as trusted candidates: {overlap}")
    return hosts


# --- the matrix, derived from the T014 inventory --------------------------------------------------

def inventory_hosts(inventory, backend):
    """Every validated host entry the T014 inventory records for a backend.

    Validation is fail-closed and happens before any live policy work: a malformed host would
    otherwise flow into the whitespace-delimited plan protocol and the in-sandbox probe script,
    where a space silently truncates the applied rule set and a metacharacter breaks (or injects
    into) the generated shell body. HOSTNAME admits only a lowercase bare hostname, which excludes
    whitespace, shell metacharacters, uppercase, a scheme and an embedded path.
    """
    entries = (inventory.get("backends", {}).get(backend) or {}).get("hosts", [])
    if not isinstance(entries, list):
        raise ValueError(f"the inventory's {backend} hosts are not a list")
    seen = set()
    for index, entry in enumerate(entries):
        where = f"{backend}.hosts[{index}]"
        if not isinstance(entry, dict):
            raise ValueError(f"{where} is not an object")
        host = entry.get("host")
        if not isinstance(host, str) or not HOSTNAME.match(host):
            raise ValueError(f"{where} host {host!r} is not a bare lowercase hostname")
        if host in seen:
            raise ValueError(f"the inventory lists {host} twice for {backend}")
        seen.add(host)
        if entry.get("port") != PORT:
            raise ValueError(f"{where} ({host}) port {entry.get('port')!r} is not {PORT}")
        required = entry.get("sandbox_required")
        if not isinstance(required, bool):
            raise ValueError(f"{where} ({host}) sandbox_required {required!r} is not a boolean")
        profiles = entry.get("profiles")
        if not isinstance(profiles, list) or any(not isinstance(v, str) for v in profiles):
            raise ValueError(f"{where} ({host}) profiles {profiles!r} is not a list of strings")
        unknown = sorted(set(profiles) - set(PROFILE_VALUES))
        if unknown:
            raise ValueError(f"{where} ({host}) names unknown profiles {unknown}")
        if len(set(profiles)) != len(profiles):
            raise ValueError(f"{where} ({host}) repeats a profile: {profiles}")
        # T014's own rule: profiles is non-empty exactly when sandbox_required is true.
        if required and not profiles:
            raise ValueError(f"{where} ({host}) is sandbox_required with no profile")
        if not required and profiles:
            raise ValueError(
                f"{where} ({host}) is not sandbox_required yet claims profiles {profiles}"
            )
    return entries


def cells(inventory, draft):
    """The four {backend} x {profile} cells, each with its expected decision per destination.

    Raises ValueError when the inventory or the draft cannot support a cell, so a malformed or
    weakened input stops the gate instead of quietly producing a smaller matrix.
    """
    candidates = trusted_candidates(draft)

    all_by_backend = {}
    required_by_backend = {}
    required_by_cell = {}
    kit_allowed_by_backend = {}
    for backend in AGENT:
        hosts = inventory_hosts(inventory, backend)
        if not hosts:
            raise ValueError(f"the inventory records no hosts for {backend}")
        all_by_backend[backend] = [h["host"] for h in hosts]
        required_by_backend[backend] = [h["host"] for h in hosts if h["sandbox_required"]]
        kit_allowed_by_backend[backend] = {h["host"] for h in hosts if h.get("source") == "kit"}
        if not required_by_backend[backend]:
            raise ValueError(f"the inventory marks no host sandbox_required for {backend}")
        # A sandbox-required host is required for a CELL only where the inventory says the sandbox
        # itself needs it. This is what keeps the documented refresh-promotion path from widening
        # untrusted: a host promoted with profiles ["trusted"] stays must-deny for untrusted.
        for profile in PROFILES:
            required_by_cell[(backend, profile)] = [
                h["host"] for h in hosts if h["sandbox_required"] and profile in h["profiles"]
            ]

    out = []
    for backend in ("claude", "codex"):
        other = "codex" if backend == "claude" else "claude"
        for profile in PROFILES:
            required = required_by_cell[(backend, profile)]
            if not required:
                raise ValueError(
                    f"the inventory marks no host sandbox_required for {backend}/{profile}, so "
                    "that cell has no control plane to prove"
                )
            permitted = list(required)
            permitted += list(candidates) if profile == "trusted" else [UNTRUSTED_GRANT]
            permitted = list(dict.fromkeys(permitted))

            # PER PROFILE. A kit host the profile permits needs no override; a kit host it does not
            # permit does, because the kit's rule is editable:false and cannot be removed. Claude
            # trusted may therefore permit code.claude.com (a documentation candidate) while Claude
            # untrusted must still deny it explicitly.
            explicit = sorted(kit_allowed_by_backend[backend] - set(permitted))

            # Every inventory host this cell does not permit is must-deny, which now includes a
            # host that is sandbox_required for the OTHER profile of the same backend.
            denied = list(all_by_backend[backend])
            denied += all_by_backend[other]
            denied += list(EXTRA_MUST_DENY)
            denied += list(candidates)
            denied = [h for h in dict.fromkeys(denied) if h not in permitted]

            expected = {host: ALLOW for host in permitted}
            for host in denied:
                expected[host] = DENY_EXPLICIT if host in explicit else DENY_IMPLICIT

            out.append({
                "name": f"dca-g4-{backend}-{profile}",
                "backend": backend,
                "profile": profile,
                "agent": AGENT[backend],
                # Every permitted host gets G4's own sandbox-scoped allow, including ones the kit
                # already allows, so the effective policy is self-sufficient and does not depend on
                # what a built-in kit happens to declare.
                "allow_rules": permitted,
                "deny_rules": explicit,
                "expected": expected,
            })
    return out


def plan_lines(cells_):
    """The matrix as the flat lines run.sh consumes, so both read one definition."""
    lines = []
    for cell in cells_:
        lines.append(f"cell {cell['name']} {cell['backend']} {cell['profile']} {cell['agent']}")
        if cell["allow_rules"]:
            lines.append(f"allow {cell['name']} {','.join(cell['allow_rules'])}")
        if cell["deny_rules"]:
            lines.append(f"deny {cell['name']} {','.join(cell['deny_rules'])}")
        for host, decision in sorted(cell["expected"].items()):
            lines.append(f"dest {cell['name']} {host} {decision}")
    return lines


# --- surface 1: the effective rule set --------------------------------------------------------------

def effective_policy_failures(cell, document):
    """Why the sandbox's effective network rules are not exactly this profile's policy.

    This is the only surface that can catch a latent allow: a rule permitting a destination no
    probe exercised. Every active network rule applicable to the sandbox must be accounted for.
    """
    if not isinstance(document, dict) or not isinstance(document.get("rules"), list):
        return ["the effective policy document could not be read"]

    name = cell["name"]
    scope = f"sandbox:{name}"
    permitted = {host for host, decision in cell["expected"].items() if decision == ALLOW}

    failures = []
    allow_entries = []          # (host, is_kit, rule id)
    denied_hosts = set()

    for index, rule in enumerate(document["rules"]):
        if not isinstance(rule, dict):
            return [f"effective rule {index} is not an object"]
        resource_type = rule.get("resource_type")
        if not isinstance(resource_type, str):
            return [f"effective rule {index} has no readable resource_type"]
        if resource_type not in OBSERVED_RESOURCE_TYPES:
            failures.append(
                f"effective rule {rule.get('id')} has resource_type {resource_type!r}, which the "
                f"pinned sbx never emitted {list(OBSERVED_RESOURCE_TYPES)}; shape drift"
            )
            continue
        if resource_type != NETWORK_RESOURCE_TYPE:
            continue

        missing = [key for key in EFFECTIVE_RULE_KEYS if key not in rule]
        if missing:
            failures.append(f"network rule {rule.get('id')} is missing {missing}; shape drift")
            continue
        resources = rule.get("resources")
        if not isinstance(resources, list) or not all(isinstance(r, str) for r in resources):
            failures.append(f"network rule {rule.get('id')} has unreadable resources")
            continue
        decision = rule.get("decision")
        if decision not in ("allow", "deny"):
            failures.append(f"network rule {rule.get('id')} has decision {decision!r}")
            continue

        if rule.get("status") != "active":
            # An inactive deny is a real hazard: under org governance local denies still apply, so
            # one reported inactive means the neutralization is not in force.
            if decision == "deny":
                failures.append(
                    f"network deny rule {rule.get('id')} is {rule.get('status')!r}, not active, "
                    f"so it does not neutralize {sorted(host_of(r) for r in resources)}"
                )
            continue

        rule_scope = rule.get("scope")
        if rule_scope == "global":
            if decision == "allow":
                failures.append(
                    f"unexpected GLOBAL network allow {rule.get('id')} for "
                    f"{sorted(host_of(r) for r in resources)}: the baseline must stay deny-all"
                )
            continue
        if rule_scope != scope:
            failures.append(
                f"network rule {rule.get('id')} is scoped to {rule_scope!r}, not to this sandbox"
            )
            continue

        # Provenance from the pinned observed shape: a built-in kit rule is named "kit:<sandbox>"
        # and is editable:false; a rule G4 created with `sbx policy allow/deny --sandbox` is
        # editable:true and named by its own uuid. Either marker alone is enough to call it a kit
        # rule, so a G4-local allow is only ever credited when BOTH say local.
        is_kit = str(rule.get("name", "")).startswith("kit:") or rule.get("editable") is False
        for resource in resources:
            if "*" in resource:
                failures.append(
                    f"network {decision} rule {rule.get('id')} uses the wildcard {resource!r}; "
                    "the V1 policy names exact hosts"
                )
                continue
            host, port = host_port_of(resource)
            if port is not None and port != PORT:
                # The V1 policy names hosts on PORT and nothing else - the inventory and the draft
                # are both validated to declare no other port. Reducing a rule to its bare host
                # here would read an allow like `github.com:22` as this profile's own 443 allow,
                # and the other three surfaces cannot object: the check, the connection attempt
                # and the log all speak only about <host>:PORT. Surface 1 is the only place a
                # port-qualified grant can be seen, so it fails closed on both decisions - a deny
                # on another port does not neutralize a kit allow on PORT either.
                failures.append(
                    f"network {decision} rule {rule.get('id')} names {resource!r}, a port other "
                    f"than {PORT}; this profile grants and denies hosts on {PORT} only"
                )
                continue
            if decision == "allow":
                allow_entries.append((host, is_kit, rule.get("id")))
            else:
                denied_hosts.add(host)

    for host, is_kit, rule_id in allow_entries:
        if host in permitted:
            continue
        if not is_kit:
            failures.append(
                f"local allow rule {rule_id} permits {host}, which this profile does not"
            )
        elif host not in denied_hosts:
            failures.append(
                f"the kit allows {host}, which this profile does not permit, and no explicit "
                "sandbox-scoped deny neutralizes it"
            )

    allowed_hosts = {host for host, _, _ in allow_entries}
    local_allowed = {host for host, is_kit, _ in allow_entries if not is_kit}
    for host in sorted(permitted):
        if host not in allowed_hosts:
            failures.append(f"{host} is permitted by this profile but no active allow rule grants it")
        elif host not in local_allowed:
            # The evidence claims the effective policy does not depend on what a built-in kit
            # happens to declare, and the production kit (T056) does not supply the built-in rule.
            # Crediting a kit allow as G4's own would make that claim untested.
            failures.append(
                f"{host} is permitted by this profile but only the built-in kit allows it; G4's "
                "own sandbox-scoped allow is absent, so the effective policy is not self-sufficient"
            )
        if host in denied_hosts:
            failures.append(f"{host} is permitted by this profile but a deny rule also matches it")

    for host in cell["deny_rules"]:
        if host not in denied_hosts:
            failures.append(
                f"the explicit deny that must neutralize the kit allow for {host} is absent"
            )
    return failures


# --- surfaces 2-4 ------------------------------------------------------------------------------------

def check_decision(document):
    """The decision `sbx policy check network --json` recorded, or None when unreadable."""
    if not isinstance(document, dict) or not isinstance(document.get("allowed"), bool):
        return None
    if document["allowed"]:
        return ALLOW
    kind = document.get("deny_kind")
    if kind == "explicit":
        return DENY_EXPLICIT
    if kind == "implicit":
        return DENY_IMPLICIT
    return None


def probe_decision(text):
    """What the in-sandbox connection attempt showed, or None when unreadable.

    The proxy answers every CONNECT. `200 Connection established` means it opened the tunnel to the
    upstream, so the policy permitted the destination; anything else means the proxy handled the
    request itself and the destination was refused. This reads the policy decision, not whether the
    upstream server happened to be healthy.
    """
    if not isinstance(text, str) or not text.strip():
        return None
    if "Connection established" in text:
        return ALLOW
    if "403" in text or "Forbidden" in text:
        return "blocked"
    return None


def log_decision(log, host):
    """How `sbx policy log --json` recorded this host, or None when it recorded nothing."""
    if not isinstance(log, dict):
        return None
    target = f"{host}:{PORT}"
    for entry in log.get("allowed_hosts") or []:
        if isinstance(entry, dict) and entry.get("host") == target:
            return ALLOW
    for entry in log.get("blocked_hosts") or []:
        if isinstance(entry, dict) and entry.get("host") == target:
            reason = entry.get("reason") or ""
            if reason == "Denied by local rule":
                return DENY_EXPLICIT
            if reason == "No matching allow rule (default deny)":
                return DENY_IMPLICIT
            return None
    return None


def logged_allowed_hosts(log):
    """Every host the log shows the sandbox actually reached, or None when unreadable."""
    if not isinstance(log, dict) or not isinstance(log.get("allowed_hosts"), list):
        return None
    out = set()
    for entry in log["allowed_hosts"]:
        if not isinstance(entry, dict) or not isinstance(entry.get("host"), str):
            return None
        out.add(entry["host"])
    return out


def cell_failures(cell, checks, probes, log):
    """Why a cell's destinations do not behave exactly as the profile requires.

    Every destination must agree across the check, the connection attempt and the log. A surface
    that could not be read is a failure, never a pass: an unreadable decision is not a permitted one.
    """
    failures = []
    for host, expected in sorted(cell["expected"].items()):
        decided = check_decision(checks.get(host))
        if decided is None:
            failures.append(f"{host}: the policy check could not be read")
        elif decided != expected:
            failures.append(f"{host}: policy check says {decided}, expected {expected}")

        observed = probe_decision(probes.get(host))
        wanted = ALLOW if expected == ALLOW else "blocked"
        if observed is None:
            failures.append(f"{host}: the in-sandbox connection attempt could not be read")
        elif observed != wanted:
            failures.append(f"{host}: the connection attempt was {observed}, expected {wanted}")

        logged = log_decision(log, host)
        if logged is None:
            failures.append(f"{host}: no matching policy-log entry")
        elif logged != expected:
            failures.append(f"{host}: the policy log says {logged}, expected {expected}")
    return failures


def surprise_failures(cell, log):
    """Destinations the sandbox actually reached that this profile never permitted."""
    reached = logged_allowed_hosts(log)
    if reached is None:
        return ["the policy log's allowed hosts could not be read"]
    permitted = {f"{host}:{PORT}" for host, decision in cell["expected"].items() if decision == ALLOW}
    extra = sorted(reached - permitted)
    return [f"the sandbox reached {host}, which this profile does not permit" for host in extra]


# --- governance, observed fail-closed ---------------------------------------------------------------

def governance_active(document, exit_code):
    """The observed governance.active boolean, or (None, why it could not be read).

    Governance is a component of the fingerprint, so an unread value must never be folded into it:
    a missing or unparseable probe would otherwise compare equal to itself across the run and let
    G4 record a digest claiming to cover a governance state it never observed.
    """
    if exit_code is None:
        return None, "the governance probe was not recorded at all"
    if not isinstance(document, dict):
        return None, f"the governance probe (exit {exit_code}) produced no readable JSON document"
    target = document.get("resource_value") or document.get("target")
    if target != GOVERNANCE_PROBE or not isinstance(document.get("allowed"), bool):
        # A usage error also exits 1, so the decided-check shape is what separates the two.
        return None, (
            f"the governance probe (exit {exit_code}) is not a decided check for "
            f"{GOVERNANCE_PROBE}: target {target!r}, allowed {document.get('allowed')!r}"
        )
    governance = document.get("governance")
    if not isinstance(governance, dict):
        return None, f"the governance probe carries no governance object: {governance!r}"
    if "active" not in governance:
        return None, "the governance object carries no active field"
    active = governance["active"]
    if not isinstance(active, bool):
        return None, f"governance.active is {active!r} ({type(active).__name__}), not a boolean"
    return active, None


def read_governance(work, which, obs):
    """(active, problem) for the before/after governance probe of this run."""
    return governance_active(
        read_json(work, f"governance-{which}.json"), obs.get(f"governance_{which}_exit")
    )


# --- the global network-policy fingerprint ---------------------------------------------------------

def fingerprint_input(policy, governance):
    """The global network-policy state a proof relies on: global rules and governance only.

    Every global network rule contributes its COMPLETE semantic shape (FINGERPRINT_RULE_KEYS), so a
    change to applies_to, resource_type, editable or any other field moves the digest. Rules are
    sorted by their own canonical form, so the value does not depend on enumeration order.

    Sandbox-scoped rules and run-scoped grants are excluded by construction, so the value does not
    move when an unrelated sandbox exists. It must still be read with no sandbox-scoped network rule
    present: the pinned sbx stops enumerating the global default-deny rule once a scoped rule
    exists, so the same global state would otherwise serialize differently.

    `governance` is the typed boolean governance.active, never a string: None returns None so an
    unobserved governance state can never be hashed.
    """
    if not isinstance(policy, dict) or not isinstance(policy.get("rules"), list):
        return None
    if not isinstance(governance, bool):
        return None
    rules = []
    for rule in policy["rules"]:
        if not isinstance(rule, dict):
            return None
        if str(rule.get("scope", "")) != "global":
            continue
        if not str(rule.get("resource_type", "")).startswith("network"):
            continue
        entry = {key: rule.get(key) for key in FINGERPRINT_RULE_KEYS}
        entry["resources"] = sorted(entry["resources"] or [])
        rules.append(entry)
    rules.sort(key=rules_module.canonical_json)
    return {"global_network_rules": rules, "governance": {"active": governance}}


def fingerprint(policy, governance):
    """'sha256:' + the digest of the canonical fingerprint input, or None when unreadable.

    ONE canonical algorithm, repeated verbatim in the evidence contract so launcher preflight and
    the T062 production re-check can recompute it: 'sha256:' + hex SHA-256 of
    canonical_json({"global_network_rules": [<every global network rule, each carrying exactly
    FINGERPRINT_RULE_KEYS with resources sorted, the list sorted by each rule's canonical JSON>],
    "governance": {"active": <boolean>}}) encoded as UTF-8. It is written to the evidence as the
    typed `network_policy_fingerprint` field, never parsed back out of prose.
    """
    value = fingerprint_input(policy, governance)
    if value is None:
        return None
    return "sha256:" + hashlib.sha256(
        rules_module.canonical_json(value).encode("utf-8")
    ).hexdigest()


# --- preflight ---------------------------------------------------------------------------------------

def preflight(obs, work):
    with open(VERSIONS, encoding="utf-8") as fh:
        pins = json.load(fh)
    version = read_json(work, "pf-version.json")
    forwarding = read_json(work, "pf-ssh-forwarding.json")
    socket_path = read_json(work, "pf-ssh-socket.json")
    policy = read_json(work, "pf-policy.json")
    listing = read_json(work, "pf-ls.json")
    refusals = preflight_module.ssh_forwarding_refusals(forwarding, socket_path)
    running = (listing or {}).get("sandboxes") if isinstance(listing, dict) else None

    return [
        (
            "G4.0a",
            "sbx client and server still match the pinned exact version and the server is running",
            obs.get("pf_version_exit") == "0"
            and preflight_module.version_pins_hold(version, pins["sbx"]["exact"]),
            f"pin {pins['sbx']['exact']}, client {(version or {}).get('client', {}).get('version')}, "
            f"server {(version or {}).get('server', {}).get('version')} "
            f"{(version or {}).get('server', {}).get('state')}",
        ),
        (
            "G4.0b",
            "the SSH-agent baseline still refuses nothing, and SSH_AUTH_SOCK is removed from every sbx call",
            obs.get("pf_ssh_forwarding_exit") == "0"
            and obs.get("pf_ssh_socket_exit") == "0"
            and not refusals
            and obs.get("sbx_env_ssh_auth_sock") == "removed",
            f"refusals {refusals or 'none'}, SSH_AUTH_SOCK in the sbx environment: "
            f"{obs.get('sbx_env_ssh_auth_sock', 'not reported')}",
        ),
        (
            "G4.0c",
            "the global network policy is exactly the G0 bootstrap deny-all rule, which G4 evaluates "
            "rather than trusts",
            obs.get("pf_policy_exit") == "0" and preflight_module.network_baseline_holds(policy),
            f"{len(preflight_module.network_rules(policy))} network rule(s): "
            f"{[r.get('id') for r in preflight_module.network_rules(policy)]}",
        ),
        (
            "G4.0d",
            "no sandbox exists before G4 creates one, so the fingerprint is read from a clean global state",
            obs.get("pf_ls_exit") == "0" and running == [],
            f"sbx ls --json: {running if running is not None else 'unreadable'}",
        ),
    ]


def input_binding_problems(obs, inventory_evidence=INVENTORY_EVIDENCE):
    """(the recorded input digests, why they do not bind G4 to the reviewed T014 state).

    Three things must hold, and none of them is about the *content* of the inventory - G4 never
    hard-codes a host list, so an intentional T014 revision stays possible. What it refuses is a
    revision that was never re-reviewed:

      1. every T014 input was tracked and clean in git when the gate started, so the files are the
         committed ones rather than arbitrary compatible JSON;
      2. the digest each file has now equals the one the run recorded, so nothing changed between
         the live policy work and this evaluation;
      3. the T014 gate evidence itself is a current PASS, so a stale or failed INVENTORY cannot
         underwrite a G4 allow set.

    A deliberate T014 revision therefore re-runs T014, re-commits the inputs and re-runs G4, which
    rewrites these digests - the explicit re-review path.
    """
    digests = input_digests()
    problems = []
    if obs.get("t014_git_clean") != "yes":
        problems.append(
            "the T014 inputs were not tracked and clean in git when the gate started "
            f"(t014_git_clean={obs.get('t014_git_clean', 'not reported')!r}; "
            f"dirty={obs.get('t014_dirty_paths', '') or 'none reported'})"
        )
    for name, _ in T014_INPUTS:
        now, recorded = digests.get(name), obs.get(f"t014_{name}")
        if now is None:
            problems.append(f"{name}: the T014 input could not be read")
        elif not recorded:
            problems.append(f"{name}: the run recorded no digest for this input")
        elif now != recorded:
            problems.append(f"{name}: changed since the run started ({recorded} -> {now})")

    evidence = read_json(os.path.dirname(inventory_evidence), os.path.basename(inventory_evidence))
    if not isinstance(evidence, dict) or evidence.get("gate") != "INVENTORY":
        problems.append("the T014 gate evidence could not be read")
    elif evidence.get("status") != "PASS":
        problems.append(f"the T014 gate evidence is {evidence.get('status')!r}, not PASS")
    else:
        try:
            with open(VERSIONS, encoding="utf-8") as fh:
                stale = rules_module.evidence_problems(evidence, json.load(fh))
        except (OSError, ValueError) as exc:
            stale = [f"the pins could not be read: {exc}"]
        if stale:
            problems.append(f"the T014 gate evidence is stale: {stale}")
    return digests, problems


# --- evaluation ------------------------------------------------------------------------------------

def evaluate(obs, work, inventory_file=INVENTORY_FILE, draft_file=DRAFT_FILE):
    rows = []
    for identifier, description, result, observed in preflight(obs, work):
        rows.append((identifier, description, result, observed, True))
    preflight_ok = all(row[2] for row in rows)

    bound, binding_problems = input_binding_problems(obs)
    rows.append((
        "G4.inputs",
        "the T014 artifacts this matrix derives from are the reviewed, committed ones: each is "
        "tracked and clean in git when the gate starts, the T014 gate evidence itself is a current "
        "PASS, and the digest of every input is recorded in the evidence, so a later edit cannot "
        "silently produce a different allow set",
        not binding_problems,
        "; ".join(binding_problems) if binding_problems else
        ", ".join(f"{name.removesuffix('_sha256')} {digest}" for name, digest in sorted(bound.items())),
        "t014_git_clean" in obs,
    ))
    inputs_ok = not binding_problems

    inventory = read_json(os.path.dirname(inventory_file), os.path.basename(inventory_file))
    draft = read_json(os.path.dirname(draft_file), os.path.basename(draft_file))
    matrix = None
    try:
        if not isinstance(inventory, dict):
            raise ValueError("the T014 inventory could not be read")
        matrix = cells(inventory, draft)
    # AttributeError belongs here with the rest: a `backends` that is a list, or a backend mapping
    # to a bare list of hosts, reaches `.get` on a non-dict. Letting it escape would propagate out
    # of record() before the evidence is written, leaving the PREVIOUS run's PASS - with its
    # fingerprint and its proven allow set - on disk for T024 and T051 to read. A malformed input
    # must produce a written FAIL, never a preserved PASS.
    except (ValueError, KeyError, TypeError, AttributeError) as exc:
        rows.append(("G4.matrix", "the G4 matrix derives from the committed T014 inventory and "
                     "trusted-allowlist draft", False,
                     f"the T014 documents could not drive the matrix: {exc}", True))
    if matrix is not None:
        candidates = trusted_candidates(draft)
        rows.append((
            "G4.matrix",
            "the G4 matrix derives from the committed T014 inventory and trusted-allowlist draft: "
            "required hosts are the entries the inventory marks sandbox_required, the trusted "
            "profile exercises every declared draft candidate, and everything else those documents "
            "name is must-deny",
            True,
            f"{len(matrix)} cells; {len(candidates)} trusted candidates exercised "
            f"({', '.join(candidates)}); untrusted grant {UNTRUSTED_GRANT}",
            True,
        ))

    for cell in matrix or []:
        name = cell["name"]
        applied = (
            obs.get(f"create_{name}_exit") == "0"
            and obs.get(f"rules_{name}_exit") == "0"
        )
        attempted = f"create_{name}_exit" in obs

        # Surface 1, evaluated on its own criterion: the effective rule set.
        effective_read = obs.get(f"effective_{name}_exit") == "0"
        effective = read_json(work, f"effective-{name}.json")
        if not effective_read:
            effective_problems = [
                f"`sbx policy ls {name} --json` exited "
                f"{obs.get(f'effective_{name}_exit', 'not reported')}, so the effective rule set "
                "is unproven"
            ]
        else:
            effective_problems = effective_policy_failures(cell, effective)
        rows.append((
            f"G4.{cell['backend']}.{cell['profile']}.effective",
            f"{cell['backend']} / {cell['profile']}: every active network rule in the effective "
            "policy is accounted for - no wildcard or unknown allow, no global allow, no rule from "
            "another sandbox, and every kit allow outside the profile is neutralized by an explicit "
            "sandbox-scoped deny",
            not effective_problems,
            "; ".join(effective_problems) if effective_problems else
            f"{sum(1 for r in effective['rules'] if r.get('resource_type') == NETWORK_RESOURCE_TYPE)}"
            f" network rule(s) inspected; every allow is either permitted by the profile or "
            f"neutralized ({len(cell['deny_rules'])} explicit deny/denies present)",
            preflight_ok and inputs_ok and attempted,
        ))

        # Surfaces 2-4.
        checks = {host: read_json(work, f"check-{name}-{host}.json") for host in cell["expected"]}
        probes = {}
        probe_text = read_file(work, f"probe-{name}.txt") or ""
        for block in probe_text.split("### host="):
            host, _, body = block.partition("\n")
            if host.strip():
                probes[host.strip()] = body
        log = read_json(work, f"log-{name}.json")

        failures = cell_failures(cell, checks, probes, log) if applied else ["the cell did not run"]
        rows.append((
            f"G4.{cell['backend']}.{cell['profile']}",
            f"{cell['backend']} / {cell['profile']}: every destination agrees across the policy "
            "check, a real connection attempt and the policy log",
            not failures,
            "; ".join(failures) if failures else
            f"{len(cell['expected'])} destinations verified on three surfaces "
            f"({sum(1 for d in cell['expected'].values() if d == ALLOW)} permitted, "
            f"{sum(1 for d in cell['expected'].values() if d != ALLOW)} denied)",
            preflight_ok and inputs_ok and applied,
        ))

        surprises = surprise_failures(cell, log) if applied else ["the cell did not run"]
        rows.append((
            f"G4.{cell['backend']}.{cell['profile']}.exact",
            f"{cell['backend']} / {cell['profile']}: the sandbox reached nothing beyond the "
            "destinations this profile permits",
            not surprises,
            "; ".join(surprises) if surprises else "no destination outside the permitted set was reached",
            preflight_ok and inputs_ok and applied,
        ))

    # The property T014 flagged: a kit allow that cannot be removed must still be overridden, and
    # the override set is computed per profile.
    kit_rows = []
    for cell in matrix or []:
        for host in cell["deny_rules"]:
            decided = check_decision(read_json(work, f"check-{cell['name']}-{host}.json"))
            kit_rows.append((cell["name"], host, decided))
    overridden = [row for row in kit_rows if row[2] != DENY_EXPLICIT]
    rows.append((
        "G4.kit-override",
        "a sandbox-scoped deny overrides the built-in kit's non-removable allow, computed per "
        "profile: every kit host a profile does not permit is denied by an explicit local rule, "
        "not merely absent from an allowlist",
        bool(kit_rows) and not overridden,
        f"{len(kit_rows)} kit-allowed host(s) explicitly denied across the claude cells: "
        + ", ".join(f"{n.rsplit('-', 1)[-1]}/{h}" for n, h, _ in kit_rows)
        if kit_rows and not overridden else
        f"not overridden: {[(n, h, d) for n, h, d in overridden]}" if overridden else
        "no kit-allowed host was available to test, so the override is unproven",
        preflight_ok and inputs_ok and matrix is not None,
    ))

    # The promotion-path hosts must still be denied everywhere.
    held = []
    for cell in matrix or []:
        for host in ("auth.openai.com", "platform.claude.com"):
            if cell["expected"].get(host) == ALLOW:
                held.append(f"{cell['name']} permits {host}")
            if check_decision(read_json(work, f"check-{cell['name']}-{host}.json")) == ALLOW:
                held.append(f"{cell['name']}: {host} was allowed")
    rows.append((
        "G4.no-promotion",
        "auth.openai.com and platform.claude.com stay must-deny in every cell: neither is promoted "
        "without the runtime evidence its documented promotion path requires",
        not held,
        "; ".join(held) if held else
        "both denied in all four cells, on the policy check and in the log",
        preflight_ok and inputs_ok and matrix is not None,
    ))

    active_before, why_before = read_governance(work, "before", obs)
    active_after, why_after = read_governance(work, "after", obs)
    governance_problems = [why for why in (why_before, why_after) if why]
    rows.append((
        "G4.governance",
        "the global governance status is observed as a typed boolean before and after the run, "
        "from the recorded probe document rather than a scraped string, and is unchanged: an "
        "unread governance status is never folded into the fingerprint",
        not governance_problems and active_before == active_after,
        "; ".join(governance_problems) if governance_problems else
        f"governance.active before {active_before!r}, after {active_after!r}"
        + ("" if active_before == active_after else " - the governance status moved"),
        f"governance_before_exit" in obs or f"governance_after_exit" in obs,
    ))

    before = fingerprint(read_json(work, "pf-policy.json"), active_before)
    after = fingerprint(read_json(work, "policy-after.json"), active_after)
    rows.append((
        "G4.fingerprint",
        "the global network-policy fingerprint is recorded as a typed field and is unchanged: G4 "
        "mutated nothing global, and every rule it added was sandbox-scoped",
        before is not None and before == after,
        f"network_policy_fingerprint {before}; after the run {after}",
        "policy_after_exit" in obs,
    ))

    remaining = read_json(work, "ls-after.json")
    names = {cell["name"] for cell in matrix or []}
    left = (
        [e for e in (remaining or {}).get("sandboxes", []) if isinstance(e, dict) and e.get("name") in names]
        if isinstance(remaining, dict)
        else None
    )
    removed = all(
        obs.get(f"rm_{name}_exit") == "0" for name in names if f"create_{name}_exit" in obs
    )
    rows.append((
        "G4.clean",
        "every G4 sandbox was removed by name, none remains, and the sandbox-scoped rules went with them",
        removed and left == [] and preflight_module.network_baseline_holds(read_json(work, "policy-after.json")),
        f"removals {[obs.get(f'rm_{n}_exit', '-') for n in sorted(names)]}, remaining {left}, "
        f"global network rules after: "
        f"{[r.get('id') for r in preflight_module.network_rules(read_json(work, 'policy-after.json'))]}",
        "ls_after_exit" in obs,
    ))

    criteria = []
    for identifier, description, result, observed, was_observed in rows:
        if was_observed:
            outcome = "PASS" if result else "FAIL"
        else:
            outcome = "NOT-RUN"
            observed = "this step did not run, because an earlier one did not hold (fail-closed)"
        criteria.append(
            {"id": identifier, "description": description, "result": outcome, "evidence_ref": observed}
        )
    return criteria, matrix


def proven_host_set(matrix):
    """The proven allow set per backend and profile: T051's input."""
    if not matrix:
        return {}
    out = {}
    for cell in matrix:
        out.setdefault(cell["backend"], {})[cell["profile"]] = sorted(
            host for host, decision in cell["expected"].items() if decision == ALLOW
        )
    return out


def restoration_digest(text):
    """'sha256:' + the digest of the durable restoration record's exact bytes."""
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()


def consumed_restoration(obs, before, prereq_file=PREREQ_FILE):
    """(the restoration record, its run binding) only when THIS run consumed it.

    A restoration is a one-time prerequisite act, not a standing property of the tree, so the
    durable record alone proves nothing: a later G4 revision that starts from an already-valid
    baseline must not inherit the claim merely because the file still exists. Three things must
    hold together, and all three are facts about this run:

      1. this run recorded a `prereq_restoration_digest` observation - it saw the record, and
         run.sh only records it when no earlier run had already consumed it;
      2. that digest still matches the bytes on disk, so the record was not edited afterwards;
      3. the baseline this run actually started from is the one the restoration produced, i.e. the
         restoration's recorded resulting fingerprint equals this run's observed `before`.

    Any of them missing means no restoration statement is made.
    """
    claimed = obs.get("prereq_restoration_digest")
    if not claimed:
        return None, None
    try:
        with open(prereq_file, encoding="utf-8") as fh:
            text = fh.read()
        restoration = json.loads(text)
    except (OSError, ValueError):
        return None, None
    if not isinstance(restoration, dict):
        return None, None
    digest = restoration_digest(text)
    if digest != claimed:
        return None, None
    produced = (restoration.get("verification") or {}).get("network_policy_fingerprint_after")
    if before is None or produced != before:
        return None, None
    return restoration, {
        "digest": digest,
        "consumed_by_this_run": True,
        "baseline_fingerprint": before,
    }


def record(obs_path, work=WORK, versions_path=VERSIONS, evidence_path=EVIDENCE):
    obs = read_observations(obs_path)
    criteria, matrix = evaluate(obs, work)
    status = "PASS" if all(row["result"] == "PASS" for row in criteria) else "FAIL"
    with open(versions_path, encoding="utf-8") as fh:
        versions = json.load(fh)
    active_before, _ = read_governance(work, "before", obs)
    active_after, _ = read_governance(work, "after", obs)
    before = fingerprint(read_json(work, "pf-policy.json"), active_before)
    after = fingerprint(read_json(work, "policy-after.json"), active_after)

    evidence = {
        "gate": "G4",
        "status": status,
        "run_at": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "versions": {
            "sbx": (read_json(work, "pf-version.json") or {}).get("client", {}).get("version") or None
        },
        "provenance": rules_module.evidence_provenance("G4", versions),
        "criteria": criteria,
        "fallback_applied": None,
        "notes": (
            "Effective per-sandbox network policy, proven for all four {claude, codex} x {trusted, "
            "untrusted} cells on four surfaces that must agree: the effective rule set itself, the "
            "policy check, a real in-sandbox connection attempt, and the policy log. The effective "
            "rule set is inspected rule by rule, because the other three only ever speak about "
            "destinations G4 thought to probe and so cannot detect a latent allow. The matrix is "
            "derived from the committed T014 inventory and trusted-allowlist draft rather than "
            "restated, and the trusted profile exercises every declared draft candidate. Claude's "
            "built-in kit allows seven hosts and its rule is editable:false, so the hosts a profile "
            "does not permit are denied by explicit sandbox-scoped rules, computed per profile; "
            "Codex's base contributes no network allow at all, so chatgpt.com is added explicitly. "
            "auth.openai.com and platform.claude.com stay must-deny in every cell. Untrusted cells "
            "get the control plane plus a single named granted host and nothing else, so untrusted "
            "never inherits the trusted allowlist. Every rule G4 added carried --sandbox; the "
            "global fingerprint is captured with zero sandboxes before and after and recorded as "
            "the typed network_policy_fingerprint field; this gate run itself never calls sbx "
            "policy init, sbx reset or sbx rm --all. Sandboxes are mountless, created with --skills off and removed "
            "one at a time by name."
        ),
    }
    digests = input_digests()
    if all(digests.values()):
        # Omitted only when an input could not be read at all - which is itself a G4.inputs FAIL,
        # so a PASS always carries the complete binding.
        evidence["inventory_inputs"] = digests

    if before is not None and before == after:
        evidence["network_policy_fingerprint"] = before

    restoration, binding = consumed_restoration(obs, before)
    if restoration is not None:
        verified = restoration.get("verification") or {}
        evidence["prerequisite_restoration"] = binding
        evidence["notes"] += (
            " PREREQUISITE RESTORATION (not a G0 proof, and gates/G0.json is neither modified nor "
            "re-run): the sandboxd VM had restarted and the global network policy had returned to "
            "the uninitialized state G0 originally found, which fails G4.0c closed. With zero "
            "sandboxes present and the pre-state recorded as uninitialized, the developer ran "
            "`sbx policy init deny-all` once - and only that - to restore the documented post-G0 "
            "prerequisite before this run. The restored rule set was then verified to be exactly "
            f"the documented bootstrap baseline ({verified.get('matches_documented_bootstrap_rule_field_by_field')}) "
            f"and the restored state to match the prior baseline ({verified.get('restored_state_matches_prior_g4_baseline')}); "
            "a difference would have stopped the rerun as FAIL rather than the expectation being "
            "adapted to it. No SSH or other global setting was touched, and sbx reset, sbx rm --all, "
            "sbx policy rm and sbx policy reset were not used. Full record: "
            f"gates/G4/prereq/restoration.json, consumed by THIS run under binding "
            f"{binding['digest']} (the run observed that record, it had not been consumed by an "
            "earlier run, and the baseline this run started from is the one it produced). A later "
            "rerun that starts from an already-valid baseline records no restoration, even though "
            "the durable record remains on disk. G4's own run adds no global rule: every rule it "
            "writes carries --sandbox."
        )
    # T051 consumes the TYPED field, never prose, and only a PASS produces one: a set nothing
    # verified must never reach runtime/policy/network.yaml.
    if status == "PASS":
        evidence["proven_network_allowset"] = proven_host_set(matrix)
    evidence["notes"] += (
        " The proven allow set per backend and profile - T051's input, and the subset T061 checks "
        "against - is the typed proven_network_allowset field, written only on PASS; it is never "
        "recorded as prose, and a FAIL carries no proven set at all."
    )
    with open(evidence_path, "w", encoding="utf-8") as fh:
        fh.write(json.dumps(evidence, indent=2, ensure_ascii=False) + "\n")
    return status, criteria


def main(argv):
    args = argv[1:]
    mode = args.pop(0) if args and args[0].startswith("--") else None
    if mode == "--inputs":
        # Emitted into observations.env before any sandbox exists, so the gate is bound to the
        # T014 state it actually ran against. Exits non-zero when an input is unreadable.
        digests = input_digests()
        for name, digest in digests.items():
            print(f"t014_{name}={digest}")
        return 0 if all(digests.values()) else 1

    if mode == "--plan":
        with open(INVENTORY_FILE, encoding="utf-8") as fh:
            inventory = json.load(fh)
        with open(DRAFT_FILE, encoding="utf-8") as fh:
            draft = json.load(fh)
        for line in plan_lines(cells(inventory, draft)):
            print(line)
        return 0

    path = args[0] if args else None
    work = args[1] if len(args) > 1 else WORK
    if path is None or mode not in (None, "--preflight"):
        print("usage: python3 gates/G4/record.py [--plan|--preflight|--inputs] "
              "<observations.env> [<work-dir>]", file=sys.stderr)
        return 2
    if mode == "--preflight":
        rows = preflight(read_observations(path), work)
        for identifier, description, result, observed in rows:
            print(f"  {identifier} {'PASS' if result else 'FAIL'} {description}")
            print(f"          observed: {observed}")
        return 0 if all(result for _, _, result, _ in rows) else 1

    status, criteria = record(path, work)
    print(f"G4: {status} ({EVIDENCE})")
    for row in criteria:
        print(f"  {row['id']:<34} {row['result']:<8} {row['description']}")
        print(f"          observed: {row['evidence_ref']}")
    return 0 if status == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
