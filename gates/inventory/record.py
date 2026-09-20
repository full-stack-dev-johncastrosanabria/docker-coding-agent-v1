"""Evaluate the control-plane host inventory and write gates/INVENTORY.json (tasks.md T014).

Standard library only, and nothing global changes. T014 is gate preparation, not a gate: it
builds the draft inventory that lets G4 (T015) tell a *documented* or provider host apart from a
host the sandboxed backend process actually requires at runtime.

The inventory files are authored from evidence; this module is the fail-closed checker that
stops them drifting from it. The check that matters is bidirectional: for each backend, the
hosts the inventory attributes to `source: kit` must be exactly the hosts the recorded
`sbx policy ls <sandbox>` output shows that backend's built-in kit declaring. An invented host
fails, and so does a silently dropped one.

Everything else fails closed too: a malformed entry, a duplicate host, an unknown purpose,
source or profile, a missing evidence_ref, a sandbox_required=true that no runtime-control-plane
evidence supports, a backend with no runtime-control-plane host, a promoted auth.openai.com, a
secret-shaped field or value, or an unreadable observation.

Usage:
  python3 gates/inventory/record.py <observations.env> [<work-dir>]   write gates/INVENTORY.json
  python3 gates/inventory/record.py --preflight <obs> [<work-dir>]    exit 0 only if the post-G0
                                                                      state still holds
"""

import datetime
import importlib.util
import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
WORK = os.path.join(ROOT, "gates", "inventory", "work")
VERSIONS = os.path.join(ROOT, "runtime", "versions.yaml")
EVIDENCE = os.path.join(ROOT, "gates", "INVENTORY.json")
HOSTS_FILE = os.path.join(ROOT, "gates", "inventory", "control-plane-hosts.json")
ALLOWLIST_FILE = os.path.join(ROOT, "gates", "inventory", "trusted-allowlist.draft.json")

BACKENDS = (("claude", "dca-inv-claude"), ("codex", "dca-inv-codex"))

PURPOSES = ("host-oauth-login", "runtime-control-plane", "refresh", "discovery")
SOURCES = ("kit", "docs", "discovery")
PROFILES = ("trusted", "untrusted")
# Only these purposes can ever be required inside the sandbox.
SANDBOX_PURPOSES = ("runtime-control-plane", "refresh")
REQUIRED_FIELDS = ("host", "port", "purpose", "sandbox_required", "profiles", "source", "evidence_ref")
OPTIONAL_FIELDS = ("path", "note")

# auth.openai.com stays host-side until G2/G3 evidence proves an in-VM refresh is required
# (research.md R13). T014 must never promote it.
PINNED_HOST_SIDE = {"auth.openai.com": "host-oauth-login"}

# The two Codex endpoints, corroborated from the pinned docker-agent artifact itself.
CODEX_RUNTIME_URL = "https://chatgpt.com/backend-api/codex"
CODEX_LOGIN_URL = "https://auth.openai.com"

SECRET_NAME = re.compile(r"(token|secret|passw|credential|authoriz|cookie|api[_-]?key|bearer)", re.IGNORECASE)
# A credential-shaped run: long and opaque. A lowercase hex digest is a hash, which the evidence
# contract explicitly permits, so it is not flagged.
OPAQUE = re.compile(r"[A-Za-z0-9_-]{32,}")
HEX = re.compile(r"^[0-9a-f]+$")
HOSTNAME = re.compile(r"^[a-z0-9]([a-z0-9-]*[a-z0-9])?(\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)+$")

PREFLIGHT = ("INV.0a", "INV.0b", "INV.0c", "INV.0d", "INV.0e")


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


# --- what the built-in kit declares -------------------------------------------------------------

def kit_network_allowances(policy, sandbox):
    """{"host:port"} that this sandbox's scoped network allow rules grant, or None if unreadable.

    None means "could not be established", never "nothing was declared": an empty set is the
    real observation that a kit declares no network at all, and the two must stay distinguishable.
    """
    if not isinstance(policy, dict) or not isinstance(policy.get("rules"), list):
        return None
    scope = f"sandbox:{sandbox}"
    allowed = set()
    for rule in policy["rules"]:
        if not isinstance(rule, dict):
            return None
        if not str(rule.get("resource_type", "")).startswith("network"):
            continue
        if rule.get("scope") != scope or rule.get("decision") != "allow":
            continue
        resources = rule.get("resources")
        if not isinstance(resources, list) or not all(isinstance(r, str) for r in resources):
            return None
        allowed.update(resources)
    return allowed


def declared_hosts(inventory, backend, source):
    """{"host:port"} the inventory attributes to this source for this backend."""
    hosts = (inventory.get("backends", {}).get(backend) or {}).get("hosts", [])
    return {
        f"{entry['host']}:{entry['port']}"
        for entry in hosts
        if isinstance(entry, dict)
        and entry.get("source") == source
        and isinstance(entry.get("host"), str)
        and isinstance(entry.get("port"), int)
    }


def kit_crosscheck_problems(inventory, observed):
    """Why the inventory's kit-sourced hosts differ from what the kit was observed declaring."""
    problems = []
    for backend, _ in BACKENDS:
        seen = observed.get(backend)
        if seen is None:
            problems.append(f"{backend}: the sandbox policy listing could not be read, so kit hosts are unproven")
            continue
        claimed = declared_hosts(inventory, backend, "kit")
        for extra in sorted(claimed - seen):
            problems.append(f"{backend}: {extra} is recorded as source=kit but the kit declared no such allowance")
        for missing in sorted(seen - claimed):
            problems.append(f"{backend}: the kit declares {missing} but the inventory does not record it")
    return problems


# --- structure and classification ---------------------------------------------------------------

def structure_problems(inventory):
    """Why the inventory document is not well formed."""
    problems = []
    backends = inventory.get("backends")
    if not isinstance(backends, dict):
        return ["the inventory has no backends object"]
    for backend, _ in BACKENDS:
        section = backends.get(backend)
        if not isinstance(section, dict) or not isinstance(section.get("hosts"), list):
            problems.append(f"{backend}: no hosts list")
            continue
        seen = {}
        for index, entry in enumerate(section["hosts"]):
            where = f"{backend}.hosts[{index}]"
            if not isinstance(entry, dict):
                problems.append(f"{where} is not an object")
                continue
            for field in REQUIRED_FIELDS:
                if field not in entry:
                    problems.append(f"{where} has no {field}")
            unknown = set(entry) - set(REQUIRED_FIELDS) - set(OPTIONAL_FIELDS)
            if unknown:
                problems.append(f"{where} has unknown field(s) {sorted(unknown)}")

            host = entry.get("host")
            if not isinstance(host, str) or not HOSTNAME.match(host):
                problems.append(f"{where} host {host!r} is not a bare lowercase hostname")
            else:
                where = f"{backend}.{host}"
                if host in seen:
                    problems.append(
                        f"{backend}: {host} appears twice (entries {seen[host]} and {index}); "
                        "a duplicate must be reconciled into one entry, not left to chance"
                    )
                seen.setdefault(host, index)

            port = entry.get("port")
            if not isinstance(port, int) or isinstance(port, bool) or not 1 <= port <= 65535:
                problems.append(f"{where} port {port!r} is not a port number")
            if entry.get("purpose") not in PURPOSES:
                problems.append(f"{where} purpose {entry.get('purpose')!r} is not one of {list(PURPOSES)}")
            if entry.get("source") not in SOURCES:
                problems.append(f"{where} source {entry.get('source')!r} is not one of {list(SOURCES)}")
            if not isinstance(entry.get("sandbox_required"), bool):
                problems.append(f"{where} sandbox_required {entry.get('sandbox_required')!r} is not a boolean")

            profiles = entry.get("profiles")
            if not isinstance(profiles, list) or not all(p in PROFILES for p in profiles):
                problems.append(f"{where} profiles {profiles!r} is not a subset of {list(PROFILES)}")
            elif len(set(profiles)) != len(profiles):
                problems.append(f"{where} profiles {profiles!r} repeats a profile")

            reference = entry.get("evidence_ref")
            if not isinstance(reference, str) or not reference.strip():
                problems.append(f"{where} has no usable evidence_ref")

            path = entry.get("path")
            if path is not None and (not isinstance(path, str) or not path.startswith("/")):
                problems.append(f"{where} path {path!r} is not an absolute documentation path")
    return problems


def classification_problems(inventory):
    """Why an entry's classification is not supported by the T014 rules."""
    problems = []
    for backend, _ in BACKENDS:
        for entry in (inventory.get("backends", {}).get(backend) or {}).get("hosts", []):
            if not isinstance(entry, dict):
                continue
            host = entry.get("host")
            where = f"{backend}.{host}"
            purpose = entry.get("purpose")
            required = entry.get("sandbox_required")
            profiles = entry.get("profiles")
            if not isinstance(required, bool) or purpose not in PURPOSES or not isinstance(profiles, list):
                continue  # structure_problems already reported it

            if required and purpose not in SANDBOX_PURPOSES:
                problems.append(
                    f"{where} is sandbox_required with purpose {purpose!r}; only "
                    f"{list(SANDBOX_PURPOSES)} can be required inside the sandbox"
                )
            if required and not profiles:
                problems.append(f"{where} is sandbox_required but names no profile that needs it")
            if not required and profiles:
                problems.append(
                    f"{where} is not sandbox_required but lists profiles {profiles}; "
                    "profiles record where the sandbox itself must reach the host"
                )
            if purpose in ("host-oauth-login", "discovery") and required:
                problems.append(f"{where} has purpose {purpose!r}, which is never sandbox_required by default")

            pinned = PINNED_HOST_SIDE.get(host)
            if pinned is not None:
                if purpose != pinned:
                    problems.append(
                        f"{where} must stay {pinned!r} at this stage; it is recorded as {purpose!r}. "
                        "Promotion needs G2/G3 evidence and its own evidence_ref (research.md R13)"
                    )
                if required:
                    problems.append(f"{where} must not be promoted to sandbox_required without gate evidence")
    return problems


def control_plane_problems(inventory):
    """Why a backend lacks a runtime-control-plane host the sandbox actually needs."""
    problems = []
    for backend, _ in BACKENDS:
        hosts = (inventory.get("backends", {}).get(backend) or {}).get("hosts", [])
        proven = [
            entry for entry in hosts
            if isinstance(entry, dict)
            and entry.get("purpose") == "runtime-control-plane"
            and entry.get("sandbox_required") is True
        ]
        if not proven:
            problems.append(
                f"{backend}: no runtime-control-plane host is recorded as sandbox_required, so the "
                "inventory cannot tell G4 what the backend needs"
            )
    return problems


def secret_problems(document, where="inventory"):
    """Why a document looks like it carries credential material."""
    problems = []

    def walk(node, path):
        if isinstance(node, dict):
            for key, value in node.items():
                if isinstance(key, str) and SECRET_NAME.search(key):
                    problems.append(f"{path}.{key} is a secret-shaped field name")
                walk(value, f"{path}.{key}")
        elif isinstance(node, list):
            for index, value in enumerate(node):
                walk(value, f"{path}[{index}]")
        elif isinstance(node, str):
            for run in OPAQUE.findall(node):
                if not HEX.match(run):
                    problems.append(f"{path} contains a credential-shaped value of {len(run)} characters")

    walk(document, where)
    return problems


def allowlist_problems(draft):
    """Why the trusted-allowlist draft is not a well-formed set of unapproved candidates."""
    if not isinstance(draft, dict):
        return ["the trusted allowlist draft is not an object"]
    problems = []
    if draft.get("approved") is not False:
        problems.append("the draft must record approved=false: G4 determines the effective allowed set")
    if draft.get("status") != "draft":
        problems.append(f"the draft status is {draft.get('status')!r}, not 'draft'")
    if draft.get("applies_to_profiles") != ["trusted"]:
        problems.append(
            f"applies_to_profiles is {draft.get('applies_to_profiles')!r}; the draft applies to the "
            "trusted profile only and must never widen untrusted access"
        )
    categories = draft.get("categories")
    if not isinstance(categories, dict) or not categories:
        problems.append("the draft declares no R12 categories")
        categories = {}
    candidates = draft.get("candidates")
    if not isinstance(candidates, list):
        return problems + ["the draft has no candidates list"]
    seen = set()
    for index, entry in enumerate(candidates):
        where = f"candidates[{index}]"
        if not isinstance(entry, dict):
            problems.append(f"{where} is not an object")
            continue
        host = entry.get("host")
        if not isinstance(host, str) or not HOSTNAME.match(host):
            problems.append(f"{where} host {host!r} is not a bare lowercase hostname")
        elif host in seen:
            problems.append(f"the draft lists {host} twice")
        else:
            seen.add(host)
        if entry.get("approved") is not False:
            problems.append(f"{where} ({host}) must record approved=false; nothing here is an allowed destination")
        if entry.get("category") not in categories:
            problems.append(f"{where} ({host}) category {entry.get('category')!r} is not a declared R12 category")
        if not isinstance(entry.get("evidence_ref"), str) or not entry.get("evidence_ref", "").strip():
            problems.append(f"{where} ({host}) has no usable evidence_ref")
        if not isinstance(entry.get("rationale"), str) or not entry.get("rationale", "").strip():
            problems.append(f"{where} ({host}) has no rationale")
        if "sandbox_required" in entry:
            problems.append(
                f"{where} ({host}) records sandbox_required; the trusted allowlist is a profile "
                "allowance, not a control-plane requirement"
            )
    return problems


# --- preflight ----------------------------------------------------------------------------------

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
            "INV.0a",
            "sbx client and server still match the pinned exact version and the server is running",
            obs.get("pf_version_exit") == "0"
            and preflight_module.version_pins_hold(version, pins["sbx"]["exact"]),
            f"pin {pins['sbx']['exact']}, client {(version or {}).get('client', {}).get('version')}, "
            f"server {(version or {}).get('server', {}).get('version')} "
            f"{(version or {}).get('server', {}).get('state')}",
        ),
        (
            "INV.0b",
            "the SSH-agent baseline still refuses nothing, and SSH_AUTH_SOCK is removed from every sbx call",
            obs.get("pf_ssh_forwarding_exit") == "0"
            and obs.get("pf_ssh_socket_exit") == "0"
            and not refusals
            and obs.get("sbx_env_ssh_auth_sock") == "removed",
            f"refusals {refusals or 'none'}, SSH_AUTH_SOCK in the sbx environment: "
            f"{obs.get('sbx_env_ssh_auth_sock', 'not reported')}",
        ),
        (
            "INV.0c",
            "the global network policy is still exactly the G0 bootstrap deny-all rule",
            obs.get("pf_policy_exit") == "0" and preflight_module.network_baseline_holds(policy),
            f"{len(preflight_module.network_rules(policy))} network rule(s): "
            f"{[r.get('id') for r in preflight_module.network_rules(policy)]}",
        ),
        (
            "INV.0d",
            "no sandbox exists before the inventory creates one",
            obs.get("pf_ls_exit") == "0" and running == [],
            f"sbx ls --json: {running if running is not None else 'unreadable'}",
        ),
        (
            "INV.0e",
            "the G6 probe kit and the pinned docker-agent artifact are present, with the pinned SHA-256",
            obs.get("kit_present") == "yes"
            and obs.get("artifact_sha256") == pins["docker_agent_artifact"]["sha256"],
            f"kit {obs.get('kit_present', 'not reported')}, artifact sha256 "
            f"{obs.get('artifact_sha256', 'not reported')} vs pin "
            f"{pins['docker_agent_artifact']['sha256']}",
        ),
    ]


# --- evaluation -----------------------------------------------------------------------------------

def evaluate(obs, work, hosts_file=HOSTS_FILE, allowlist_file=ALLOWLIST_FILE):
    rows = []
    for identifier, description, result, observed in preflight(obs, work):
        rows.append((identifier, description, result, observed, True))
    preflight_ok = all(row[2] for row in rows)

    inventory = read_json(os.path.dirname(hosts_file), os.path.basename(hosts_file))
    draft = read_json(os.path.dirname(allowlist_file), os.path.basename(allowlist_file))

    observed_kit = {}
    for backend, sandbox in BACKENDS:
        policy = read_json(work, f"policy-{backend}.json")
        # The step ran as soon as the run reached the create. A create or a policy read that was
        # attempted and failed is a FAIL: NOT-RUN is reserved for a step that never executed.
        attempted = f"create_{sandbox}_exit" in obs
        read_ok = obs.get(f"create_{sandbox}_exit") == "0" and obs.get(f"policy_{sandbox}_exit") == "0"
        allowances = kit_network_allowances(policy, sandbox) if read_ok else None
        observed_kit[backend] = allowances
        rows.append(
            (
                f"INV.1.{backend}",
                f"{backend}: the built-in kit's sandbox-scoped network declarations were read from a "
                "kit-only sandbox holding no repository code",
                allowances is not None,
                f"create exit {obs.get(f'create_{sandbox}_exit', '-')}, policy ls exit "
                f"{obs.get(f'policy_{sandbox}_exit', '-')}, declared "
                f"{sorted(allowances) if allowances is not None else 'unreadable'}",
                preflight_ok and attempted,
            )
        )

    problems = structure_problems(inventory) if isinstance(inventory, dict) else ["the inventory could not be read"]
    rows.append(
        (
            "INV.2",
            "every inventory entry is well formed: host, port, purpose, sandbox_required, profiles, "
            "source and evidence_ref, with no duplicate or unknown value",
            not problems,
            "; ".join(problems) if problems else "all entries well formed",
            preflight_ok and isinstance(inventory, dict),
        )
    )

    problems = classification_problems(inventory) if isinstance(inventory, dict) else ["unreadable"]
    rows.append(
        (
            "INV.3",
            "every classification follows the T014 rules: sandbox_required only for a runtime "
            "control plane or refresh host, profiles exactly where the sandbox needs it, and "
            "host-oauth-login and discovery hosts never required",
            not problems,
            "; ".join(problems) if problems else "all classifications supported",
            preflight_ok and isinstance(inventory, dict),
        )
    )

    problems = kit_crosscheck_problems(inventory, observed_kit) if isinstance(inventory, dict) else ["unreadable"]
    rows.append(
        (
            "INV.4",
            "the hosts recorded as source=kit are exactly the hosts the kits were observed declaring, "
            "in both directions",
            not problems,
            "; ".join(problems) if problems else "kit-sourced hosts match the observed declarations exactly",
            preflight_ok and isinstance(inventory, dict) and all(v is not None for v in observed_kit.values()),
        )
    )

    problems = control_plane_problems(inventory) if isinstance(inventory, dict) else ["unreadable"]
    rows.append(
        (
            "INV.5",
            "every backend has at least one runtime-control-plane host recorded as sandbox_required",
            not problems,
            "; ".join(problems) if problems else "claude and codex each have a required runtime control plane",
            preflight_ok and isinstance(inventory, dict),
        )
    )

    rows.append(
        (
            "INV.6",
            "the Codex endpoints come from the pinned docker-agent artifact itself, not from a note "
            "about it",
            obs.get("artifact_runtime_url") == "yes" and obs.get("artifact_login_url") == "yes",
            f"{CODEX_RUNTIME_URL} present: {obs.get('artifact_runtime_url', 'not reported')}, "
            f"{CODEX_LOGIN_URL} present: {obs.get('artifact_login_url', 'not reported')}",
            preflight_ok and "artifact_runtime_url" in obs,
        )
    )

    problems = allowlist_problems(draft)
    rows.append(
        (
            "INV.7",
            "the trusted allowlist draft is a set of unapproved candidates in declared R12 categories, "
            "scoped to the trusted profile",
            not problems,
            "; ".join(problems) if problems else
            f"{len(draft.get('candidates', []))} candidates, none approved",
            preflight_ok and isinstance(draft, dict),
        )
    )

    problems = secret_problems(inventory, "control-plane-hosts") + secret_problems(draft, "trusted-allowlist.draft")
    rows.append(
        (
            "INV.8",
            "neither inventory file carries a secret-shaped field or value",
            not problems,
            "; ".join(problems) if problems else "no secret-shaped field or value",
            preflight_ok and isinstance(inventory, dict) and isinstance(draft, dict),
        )
    )

    removed = all(obs.get(f"rm_{sandbox}_exit") == "0" for _, sandbox in BACKENDS if f"create_{sandbox}_exit" in obs)
    remaining = read_json(work, "ls-after.json")
    names = {sandbox for _, sandbox in BACKENDS}
    left = (
        [e for e in (remaining or {}).get("sandboxes", []) if isinstance(e, dict) and e.get("name") in names]
        if isinstance(remaining, dict)
        else None
    )
    rows.append(
        (
            "INV.clean",
            "every sandbox the inventory created was removed by name and none remains",
            removed and left == [],
            f"removals {[obs.get(f'rm_{s}_exit', '-') for _, s in BACKENDS]}, remaining inventory "
            f"sandboxes {left if left is not None else 'unreadable'}",
            "ls_after_exit" in obs,
        )
    )

    after = read_json(work, "policy-after.json")
    rows.append(
        (
            "INV.baseline",
            "the global network policy is unchanged: still exactly the G0 bootstrap deny-all rule",
            obs.get("policy_after_exit") == "0" and preflight_module.network_baseline_holds(after),
            f"{len(preflight_module.network_rules(after))} network rule(s) after the run: "
            f"{[r.get('id') for r in preflight_module.network_rules(after)]}",
            "policy_after_exit" in obs,
        )
    )

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
    return criteria


def record(obs_path, work=WORK, versions_path=VERSIONS, evidence_path=EVIDENCE):
    obs = read_observations(obs_path)
    criteria = evaluate(obs, work)
    status = "PASS" if all(row["result"] == "PASS" for row in criteria) else "FAIL"
    with open(versions_path, encoding="utf-8") as fh:
        versions = json.load(fh)
    evidence = {
        "gate": "INVENTORY",
        "status": status,
        "run_at": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "versions": {
            "sbx": (read_json(work, "pf-version.json") or {}).get("client", {}).get("version") or None
        },
        "provenance": rules_module.evidence_provenance("INVENTORY", versions),
        "criteria": criteria,
        "fallback_applied": None,
        "notes": (
            "T014 is gate preparation, not a gate: it builds the draft inventory that lets G4 tell a "
            "documented or provider host apart from one the sandboxed backend process requires. "
            "Sources were used in priority order and discovery was NOT needed for either backend: the "
            "built-in claude kit declares a sandbox-scoped allow rule covering seven hosts, of which "
            "only api.anthropic.com carries task traffic, and the built-in docker-agent kit declares "
            "no network rule at all, so Codex's endpoints come from the pinned docker-agent artifact "
            "this project ships. No backend prompt was run, no credential was used and none was "
            "recorded. Nothing here is a proven allowance: G4 decides the effective per-sandbox "
            "policy, and the six non-required Claude kit hosts are must-deny candidates for it. "
            "Both sandboxes were mountless, created with --skills off, held no repository code, and "
            "were created and removed one at a time by name; sbx rm --all and sbx reset are never "
            "used, and nothing global changed."
        ),
    }
    with open(evidence_path, "w", encoding="utf-8") as fh:
        fh.write(json.dumps(evidence, indent=2, ensure_ascii=False) + "\n")
    return status, criteria


def main(argv):
    args = argv[1:]
    mode = args.pop(0) if args and args[0].startswith("--") else None
    path = args[0] if args else None
    work = args[1] if len(args) > 1 else WORK
    if path is None or mode not in (None, "--preflight"):
        print("usage: python3 gates/inventory/record.py [--preflight] <observations.env> [<work-dir>]",
              file=sys.stderr)
        return 2
    if mode == "--preflight":
        rows = preflight(read_observations(path), work)
        for identifier, description, result, observed in rows:
            print(f"  {identifier} {'PASS' if result else 'FAIL'} {description}")
            print(f"          observed: {observed}")
        return 0 if all(result for _, _, result, _ in rows) else 1

    status, criteria = record(path, work)
    print(f"INVENTORY: {status} ({EVIDENCE})")
    for row in criteria:
        print(f"  {row['id']:<18} {row['result']:<8} {row['description']}")
        print(f"          observed: {row['evidence_ref']}")
    return 0 if status == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
