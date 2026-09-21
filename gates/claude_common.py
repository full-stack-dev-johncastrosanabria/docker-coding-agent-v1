"""Shared preflight and evidence helpers for the Claude backend gates (tasks.md T016-T018, T021).

G1a, G1c, G1d and G1b all create mountless sandboxes from the same pinned `sandbox_bases.claude`
base, under the network policy G4 accepted, with the same SSH discipline and the same cleanup
rule. Each therefore has to answer the same questions before it may claim anything, and this
module answers them once. Four parallel copies of one parser is precisely how three gates end up
agreeing while the fourth quietly drifts, so the Claude block keeps a single implementation and
the per-gate recorders differ only where the gates genuinely differ.

Two checks the earlier gates did not need are added here:

  * the live global network-policy fingerprint must still equal the one the ACCEPTED G4 evidence
    recorded, so no Claude gate can prove a property under a policy that is no longer the policy
    G4 verified; and
  * the base a gate actually created its sandbox from must be the exact pinned Claude base, read
    back from sbx rather than assumed, so no gate in this block can silently substitute one.

The canonical fingerprint algorithm is not restated. It is imported from the accepted G4 recorder,
which is the only definition of it in the tree, so a Claude gate and G4 can never disagree about
what the global network state hashes to.

Row shape is the one every gate recorder already uses: (id, description, result, observed).
"""

import importlib.util
import json
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
VERSIONS = os.path.join(ROOT, "runtime", "versions.yaml")
G4_EVIDENCE = os.path.join(ROOT, "gates", "G4.json")


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


preflight_module = _load("dca_preflight", os.path.join(ROOT, "gates", "preflight.py"))
rules_module = _load("dca_eligibility_rules", os.path.join(ROOT, "gates", "eligibility_rules.py"))
# The accepted G4 recorder owns the fingerprint canonicalization and the governance probe shape.
# Importing it is deliberate: a second implementation here could drift from the one the accepted
# evidence was produced with, and the whole point of the check is that the two agree.
g4_module = _load("dca_g4_record", os.path.join(ROOT, "gates", "G4", "record.py"))

GOVERNANCE_PROBE = g4_module.GOVERNANCE_PROBE
fingerprint = g4_module.fingerprint
governance_active = g4_module.governance_active


# --- capture io ---------------------------------------------------------------------------------

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


def read_versions(path=VERSIONS):
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


# --- the pinned Claude base ----------------------------------------------------------------------

def claude_base(versions):
    """(image reference, exact digest) for the pinned Claude sandbox base."""
    base = (versions.get("sandbox_bases") or {}).get("claude") or {}
    return base.get("base"), base.get("version")


def base_identity_row(gate, obs, work, versions, observed_key="sbx_resolved_base"):
    """The gate created its sandboxes from the exact pinned Claude base, read back from sbx.

    Identity is the combination the store actually exposes: the sbx-resolved reference, the
    cached template's repository/tag/id, and the full pinned digest. A tag alone identifies
    nothing, and the short image id is not a digest, so neither is accepted on its own.
    """
    image, digest = claude_base(versions)
    resolved = obs.get(observed_key)
    repository, _, tag = (image or "").rpartition(":")
    cached = preflight_module.template_image(read_json(work, "templates.json"), repository, tag)
    holds = (
        bool(image)
        and bool(digest)
        and resolved == image
        and preflight_module.template_identity_matches(cached, image, digest)
    )
    return (
        f"{gate}.base",
        "every sandbox in this gate came from the exact pinned sandbox_bases.claude base: the "
        "reference sbx resolved, the cached template's repository, tag and image id, and the "
        "pinned digest all agree; no base was discovered or substituted",
        holds,
        f"pinned {image} {digest}; sbx resolved {resolved or 'not reported'}; cached template "
        f"{cached.get('repository') + ':' + cached.get('tag') + ' id ' + str(cached.get('id')) if isinstance(cached, dict) else 'not found in the template store'}",
    )


# --- the accepted G4 policy this block runs under -------------------------------------------------

def claude_policy(profile="trusted"):
    """(allow hosts, explicit deny hosts) for the Claude profile, as accepted G4 defines it.

    The policy is not restated here. It is the same cell G4 derived from the committed T014
    inventory and trusted-allowlist draft and then proved, so a Claude gate cannot run under a
    policy that merely resembles the verified one. The denies are the built-in kit's hosts this
    profile does not permit: the kit rule is editable:false, so they are neutralized rather than
    removed.
    """
    with open(g4_module.INVENTORY_FILE, encoding="utf-8") as fh:
        inventory = json.load(fh)
    with open(g4_module.DRAFT_FILE, encoding="utf-8") as fh:
        draft = json.load(fh)
    for cell in g4_module.cells(inventory, draft):
        if cell["backend"] == "claude" and cell["profile"] == profile:
            return list(cell["allow_rules"]), list(cell["deny_rules"])
    raise ValueError(f"accepted G4 defines no claude/{profile} cell")


LOGIN_PURPOSE = "host-oauth-login"


def claude_login_hosts():
    """The documented host-oauth-login destinations T014 recorded for Claude.

    Read from the inventory rather than named here, so the set a login sandbox may reach is the
    reviewed one and a new login host cannot be introduced by a gate.
    """
    with open(g4_module.INVENTORY_FILE, encoding="utf-8") as fh:
        inventory = json.load(fh)
    hosts = (inventory.get("backends", {}).get("claude") or {}).get("hosts") or []
    return [h["host"] for h in hosts
            if isinstance(h, dict) and h.get("purpose") == LOGIN_PURPOSE]


def claude_login_policy():
    """(allow, deny, the login hosts) for a LOGIN-ONLY sandbox carrying no repository code.

    T016 permits the one-time `/login` to reach a documented host-oauth-login destination from
    inside the login sandbox, and requires it to be recorded and never to enter a run profile.
    The trusted profile explicitly denies two of those hosts - the kit allows them and the
    profile neutralizes them - so a login sandbox cannot simply add an allow on top: the deny is
    dropped for this sandbox alone. Nothing here changes what any other sandbox gets.
    """
    allow, deny = claude_policy("trusted")
    login = claude_login_hosts()
    return (
        list(dict.fromkeys(list(allow) + list(login))),
        [host for host in deny if host not in login],
        list(login),
    )


def policy_row(gate, obs, profile="trusted", g4_path=G4_EVIDENCE):
    """The policy this gate applied is exactly the one accepted G4 proved for that profile."""
    problems = []
    try:
        allow, deny = claude_policy(profile)
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as exc:
        return (
            f"{gate}.policy",
            f"the sandboxes ran under the accepted G4 claude/{profile} network policy",
            False, f"the accepted policy could not be derived: {exc}",
        )
    try:
        with open(g4_path, encoding="utf-8") as fh:
            proven = (json.load(fh).get("proven_network_allowset") or {}).get("claude", {})
    except (OSError, ValueError):
        proven = {}
    if sorted(allow) != sorted(proven.get(profile) or []):
        problems.append(
            f"the derived allow set does not match accepted G4's proven set for claude/{profile}"
        )
    applied_allow = (obs.get("policy_allow") or "").split(",") if obs.get("policy_allow") else []
    applied_deny = (obs.get("policy_deny") or "").split(",") if obs.get("policy_deny") else []
    if applied_allow != allow:
        problems.append(f"the applied allow list {applied_allow} is not {allow}")
    if applied_deny != deny:
        problems.append(f"the applied deny list {applied_deny} is not {deny}")
    if obs.get("policy_rules_exit") != "0":
        problems.append(
            f"applying the policy exited {obs.get('policy_rules_exit', 'not reported')!r}"
        )
    return (
        f"{gate}.policy",
        f"every sandbox ran under the accepted G4 claude/{profile} network policy: the allow set "
        f"is the one G4 proved, and the built-in kit's non-permitted hosts are neutralized by "
        "explicit sandbox-scoped denies",
        not problems,
        "; ".join(problems) if problems else
        f"{len(allow)} allow ({', '.join(allow)}); {len(deny)} explicit deny ({', '.join(deny)})",
    )


def accepted_network_state(g4_path=G4_EVIDENCE, versions=None):
    """(the fingerprint accepted G4 recorded, why it may not be relied on).

    A Claude gate proves its property under a network policy. That policy is only meaningful if
    it is still the one G4 verified, so the accepted evidence must be a current PASS and must
    carry the typed fingerprint. Prose is never parsed.
    """
    try:
        with open(g4_path, encoding="utf-8") as fh:
            evidence = json.load(fh)
    except (OSError, ValueError):
        return None, "the accepted G4 evidence could not be read"
    if not isinstance(evidence, dict) or evidence.get("gate") != "G4":
        return None, "the accepted G4 evidence is not a G4 document"
    if evidence.get("status") != "PASS":
        return None, f"the accepted G4 evidence is {evidence.get('status')!r}, not PASS"
    if versions is not None:
        stale = rules_module.evidence_problems(evidence, versions)
        if stale:
            return None, f"the accepted G4 evidence is stale: {stale}"
    value = evidence.get("network_policy_fingerprint")
    if not isinstance(value, str) or not value.startswith("sha256:"):
        return None, "the accepted G4 evidence carries no typed network_policy_fingerprint"
    return value, None


# --- the preflight every Claude gate shares --------------------------------------------------------

def preflight(gate, obs, work, versions=None, g4_path=G4_EVIDENCE):
    """The read-only state every Claude-block gate requires before it creates anything.

    All of it is captured with zero sandboxes present, so the fingerprint is read from the same
    clean global state G4 recorded its own from.
    """
    if versions is None:
        versions = read_versions()
    version = read_json(work, "pf-version.json")
    forwarding = read_json(work, "pf-ssh-forwarding.json")
    socket_path = read_json(work, "pf-ssh-socket.json")
    policy = read_json(work, "pf-policy.json")
    listing = read_json(work, "pf-ls.json")
    refusals = preflight_module.ssh_forwarding_refusals(forwarding, socket_path)
    running = listing.get("sandboxes") if isinstance(listing, dict) else None

    accepted, why_accepted = accepted_network_state(g4_path, versions)
    active, why_governance = governance_active(
        read_json(work, "governance-before.json"), obs.get("governance_before_exit")
    )
    live = fingerprint(policy, active)
    if why_accepted:
        fingerprint_problem = why_accepted
    elif why_governance:
        fingerprint_problem = why_governance
    elif live is None:
        fingerprint_problem = "the live global network policy could not be fingerprinted"
    elif live != accepted:
        fingerprint_problem = (
            f"the global network policy has drifted from the one G4 accepted: {accepted} -> {live}"
        )
    else:
        fingerprint_problem = None

    return [
        (
            f"{gate}.0a",
            "sbx client and server still match the pinned exact version and the server is running",
            obs.get("pf_version_exit") == "0"
            and preflight_module.version_pins_hold(version, versions["sbx"]["exact"]),
            f"pin {versions['sbx']['exact']}, client "
            f"{(version or {}).get('client', {}).get('version')}, server "
            f"{(version or {}).get('server', {}).get('version')} "
            f"{(version or {}).get('server', {}).get('state')}",
        ),
        (
            f"{gate}.0b",
            "the SSH-agent baseline still refuses nothing, and SSH_AUTH_SOCK is removed from "
            "every sbx call",
            obs.get("pf_ssh_forwarding_exit") == "0"
            and obs.get("pf_ssh_socket_exit") == "0"
            and not refusals
            and obs.get("sbx_env_ssh_auth_sock") == "removed",
            f"refusals {refusals or 'none'}, SSH_AUTH_SOCK in the sbx environment: "
            f"{obs.get('sbx_env_ssh_auth_sock', 'not reported')}",
        ),
        (
            f"{gate}.0c",
            "the global network policy is exactly the G0 bootstrap deny-all rule, which this "
            "gate evaluates rather than trusts",
            obs.get("pf_policy_exit") == "0"
            and preflight_module.network_baseline_holds(policy),
            f"{len(preflight_module.network_rules(policy))} network rule(s): "
            f"{[r.get('id') for r in preflight_module.network_rules(policy)]}",
        ),
        (
            f"{gate}.0d",
            "no sandbox exists before this gate creates one, so the fingerprint is read from a "
            "clean global state and nothing unrelated is running alongside it",
            obs.get("pf_ls_exit") == "0" and running == [],
            f"sbx ls --json: {running if running is not None else 'unreadable'}",
        ),
        (
            f"{gate}.0e",
            "the live global network-policy fingerprint still equals the one the accepted G4 "
            "evidence recorded, and that evidence is a current PASS: this gate proves its "
            "property under the policy G4 actually verified, not a drifted one",
            not fingerprint_problem,
            fingerprint_problem if fingerprint_problem else
            f"network_policy_fingerprint {live}, matching accepted G4",
        ),
    ]


PREFLIGHT_SUFFIXES = ("0a", "0b", "0c", "0d", "0e")


def preflight_ids(gate):
    return tuple(f"{gate}.{suffix}" for suffix in PREFLIGHT_SUFFIXES)


# --- shared closing criteria -----------------------------------------------------------------------

def fingerprint_unchanged_row(gate, obs, work):
    """This gate mutated nothing global: the fingerprint is identical before and after."""
    active_before, why_before = governance_active(
        read_json(work, "governance-before.json"), obs.get("governance_before_exit"))
    active_after, why_after = governance_active(
        read_json(work, "governance-after.json"), obs.get("governance_after_exit"))
    before = fingerprint(read_json(work, "pf-policy.json"), active_before)
    after = fingerprint(read_json(work, "policy-after.json"), active_after)
    problems = [why for why in (why_before, why_after) if why]
    return (
        f"{gate}.fingerprint",
        "the global network-policy fingerprint is unchanged and the governance status is "
        "observed as a typed boolean before and after: this gate added no global rule and "
        "changed no global setting",
        not problems and before is not None and before == after
        and active_before == active_after,
        "; ".join(problems) if problems else
        f"fingerprint {before} -> {after}; governance.active {active_before!r} -> {active_after!r}",
    ), before, after


def cleanup_row(gate, obs, work, names):
    """Every sandbox this gate created was removed by name and none remains."""
    remaining = read_json(work, "ls-after.json")
    left = (
        [e for e in remaining.get("sandboxes", [])
         if isinstance(e, dict) and e.get("name") in set(names)]
        if isinstance(remaining, dict) else None
    )
    attempted = [n for n in names if f"create_{n}_exit" in obs]
    removed = bool(attempted) and all(obs.get(f"rm_{n}_exit") == "0" for n in attempted)
    return (
        f"{gate}.clean",
        "every sandbox this gate created was removed by name, none remains, and the global "
        "network baseline is intact afterwards",
        removed and left == []
        and preflight_module.network_baseline_holds(read_json(work, "policy-after.json")),
        f"removals {[(n, obs.get(f'rm_{n}_exit', '-')) for n in sorted(attempted)] or 'none attempted'}, "
        f"remaining {left}, global network rules after: "
        f"{[r.get('id') for r in preflight_module.network_rules(read_json(work, 'policy-after.json'))]}",
    )


def criteria_rows(rows):
    """Turn (id, description, result, observed, was_observed) rows into evidence criteria.

    A step that did not run because an earlier one did not hold is NOT-RUN, never PASS: the
    fail-closed rule the whole gate suite shares.
    """
    criteria = []
    for identifier, description, result, observed, was_observed in rows:
        if was_observed:
            outcome = "PASS" if result else "FAIL"
        else:
            outcome = "NOT-RUN"
            observed = "this step did not run, because an earlier one did not hold (fail-closed)"
        criteria.append({
            "id": identifier, "description": description,
            "result": outcome, "evidence_ref": observed,
        })
    return criteria
