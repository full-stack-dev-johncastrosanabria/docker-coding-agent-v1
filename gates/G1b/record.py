"""Evaluate G1b and write gates/G1b.json (tasks.md T021): Claude secret unreadability.

Standard library only. G1a proved that a completely fresh sandbox authenticates with no /login and
no API key, so SOMETHING reaches the VM. G1b asks the security half of Docker's claim (E8): can a
workload process WITH SUDO read real Claude OAuth token material?

Sudo is deliberate. In this architecture the agent already has sudo and full read access to the VM
filesystem, so a gate that only showed an unprivileged user cannot read a secret would prove
almost nothing.

WHAT IS RECORDED: pattern ids, location ids, found/not-found and match counts. Never a token
value, a partial value, a cookie, an Authorization header, the contents of a secret file, or a
HASH of a candidate - a hash of a guessable secret is itself a disclosure, and of a real one it is
a verifier. The scan runs inside the VM and emits only counts and booleans.

Two controls make a negative result mean something:

  * a CANARY with a real prefix is planted and must be DETECTED, because "found nothing" is
    otherwise indistinguishable from a scanner that cannot find anything; and
  * the agent must still AUTHENTICATE and execute in the same configuration, because an isolated
    sandbox that cannot log in at all would satisfy the scan trivially.

FAIL does not make Claude unavailable. It makes Claude trusted-only for the later G9 and
eligibility decisions, and the evidence says so in a typed field rather than in prose.

Usage:
  python3 gates/G1b/record.py --policy allow|deny        emit the accepted G4 claude/trusted rules
  python3 gates/G1b/record.py --preflight <obs> [<work>] exit 0 only if the shared state holds
  python3 gates/G1b/record.py <obs> [<work>]             write gates/G1b.json
"""

import datetime
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "gates"))

import claude_common as common  # noqa: E402

WORK = os.path.join(ROOT, "gates", "G1b", "work")
EVIDENCE = os.path.join(ROOT, "gates", "G1b.json")
VERSIONS = os.path.join(ROOT, "runtime", "versions.yaml")

GATE = "G1b"
SANDBOX = "dca-g1b"
MARKER = "DCA-G1B-OK"

# Locations T021 names. Every one must be scanned and must come back clean.
LOCATIONS = ("home", "etc", "tmp", "run", "environment", "proc-environ")
# The patterns that would constitute real Claude OAuth token material if found.
TOKEN_PATTERNS = ("claude-oauth-access", "claude-oauth-refresh", "anthropic-api-key",
                  "anthropic-admin-key", "authorization-bearer", "claude-session-cookie",
                  "oauth-refresh-field", "oauth-access-field",
                  # The shapes the pinned Claude Code really writes. Omitting these is what made
                  # the first scan report a clean home directory while the credential store sat
                  # in it.
                  "claude-oauth-store", "oauth-access-camel", "oauth-refresh-camel")
CANARY_PATTERN = "claude-oauth-access"

SAFE_AUTH_FIELDS = ("loggedIn", "authMethod", "apiProvider", "subscriptionType")


def scan_results(work, name):
    """{(location, pattern): (found, matches)} plus {location: status}, or None when unreadable."""
    raw = common.read_file(work, name)
    if not isinstance(raw, str) or not raw.strip():
        return None, None
    findings, statuses = {}, {}
    for line in raw.splitlines():
        line = line.strip()
        if not line.startswith("scan "):
            continue
        fields = dict(
            part.split("=", 1) for part in line.split()[1:] if "=" in part
        )
        location = fields.get("location")
        if not location:
            continue
        if "status" in fields:
            statuses[location] = fields["status"]
            continue
        pattern = fields.get("pattern")
        if pattern:
            findings[(location, pattern)] = (
                fields.get("found") == "yes", int(fields.get("matches") or 0))
    return (findings or None), (statuses or None)


def key_values(work, name):
    raw = common.read_file(work, name)
    if not isinstance(raw, str) or not raw.strip():
        return None
    out = {}
    for line in raw.splitlines():
        key, sep, value = line.strip().partition("=")
        if sep:
            out[key.strip()] = value
    return out or None


def task_answer(work):
    raw = common.read_file(work, "task.json")
    if not isinstance(raw, str):
        return None
    parts = []
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if isinstance(event, dict) and event.get("type") == "agent_choice" \
                and isinstance(event.get("content"), str):
            parts.append(event["content"])
    return "".join(parts) if parts else None


def evaluate(obs, work, versions=None, g4_path=common.G4_EVIDENCE):
    if versions is None:
        versions = common.read_versions(VERSIONS)
    rows = []
    for row in common.preflight(GATE, obs, work, versions, g4_path):
        rows.append((*row, True))
    preflight_ok = all(row[2] for row in rows)

    base = common.base_identity_row(GATE, obs, work, versions)
    rows.append((*base, preflight_ok and "templates_exit" in obs))
    policy = common.policy_row(GATE, obs, "trusted", g4_path)
    rows.append((*policy, preflight_ok and "policy_allow" in obs))
    bindings_ok = preflight_ok and base[2] and policy[2]
    ran = bindings_ok and obs.get(f"create_{SANDBOX}_exit") == "0"

    # --- control 1: the agent still authenticates and executes here -------------------------------
    auth = common.read_json(work, "auth.json")
    answer = task_answer(work)
    auth_problems = []
    if not isinstance(auth, dict) or not all(k in auth for k in SAFE_AUTH_FIELDS):
        auth_problems.append("the safe auth projection could not be read")
    else:
        if auth["loggedIn"] is not True:
            auth_problems.append(f"loggedIn is {auth['loggedIn']!r}")
        if auth["authMethod"] != "claude.ai":
            auth_problems.append(f"authMethod is {auth['authMethod']!r}, not a subscription login")
        if auth["apiProvider"] != "firstParty":
            auth_problems.append(f"apiProvider is {auth['apiProvider']!r}")
    if obs.get("task_exit") != "0":
        auth_problems.append(f"the trivial task exited {obs.get('task_exit')!r}")
    if answer is None or MARKER not in answer:
        auth_problems.append("the model did not return the expected marker")
    rows.append((
        f"{GATE}.authenticates",
        "control: the agent still authenticates and executes in exactly the configuration the "
        "scan then runs against - a sandbox that could not log in at all would satisfy the scan "
        "trivially",
        not auth_problems,
        "; ".join(auth_problems) if auth_problems else
        f"loggedIn={auth['loggedIn']!r}, authMethod={auth['authMethod']!r}, "
        f"apiProvider={auth['apiProvider']!r}; the task returned {MARKER}",
        ran and "auth_exit" in obs,
    ))

    # --- the workload really does have sudo ---------------------------------------------------------
    privs = key_values(work, "privs.txt")
    priv_problems = []
    if privs is None:
        priv_problems.append("the privilege capture could not be read")
    else:
        if privs.get("sudo_available") != "yes":
            priv_problems.append("sudo is not available to the workload")
        if privs.get("sudo_root") != "root":
            priv_problems.append(
                f"sudo did not yield root (got {privs.get('sudo_root')!r}), so the scan was not "
                "the privileged one T021 requires")
    rows.append((
        f"{GATE}.privileged",
        "the scanning workload really does have sudo and resolves to root: T021 asks whether a "
        "PRIVILEGED process can read token material, and an unprivileged failure would not "
        "answer that",
        not priv_problems,
        "; ".join(priv_problems) if priv_problems else
        f"workload user {privs.get('whoami')!r}, sudo resolves to {privs.get('sudo_root')!r}",
        ran and "privs_exit" in obs,
    ))

    # --- control 2: the canary proves the scanner can detect a token ---------------------------------
    canary, canary_status = scan_results(work, "scan-canary.txt")
    canary_problems = []
    if canary is None:
        canary_problems.append("the canary scan could not be read")
    else:
        found, matches = canary.get(("tmp", CANARY_PATTERN), (False, 0))
        if not found or matches < 1:
            canary_problems.append(
                f"the planted canary was NOT detected by {CANARY_PATTERN}, so a clean scan below "
                "would prove nothing about isolation"
            )
        if (canary_status or {}).get("tmp") != "ok":
            canary_problems.append(f"the canary scan reported status {(canary_status or {}).get('tmp')!r}")
    rows.append((
        f"{GATE}.canary",
        "control: a clearly synthetic token with a real prefix, planted in the VM, IS detected by "
        "the scanner - so a negative result below is isolation rather than a scanner that cannot "
        "find anything",
        not canary_problems,
        "; ".join(canary_problems) if canary_problems else
        f"the canary was detected by {CANARY_PATTERN} in tmp "
        f"({canary[('tmp', CANARY_PATTERN)][1]} match(es))",
        ran and "scan_canary_exit" in obs,
    ))

    # --- the real scan ---------------------------------------------------------------------------------
    findings, statuses = scan_results(work, "scan.txt")
    scan_problems = []
    readable = []
    if findings is None or statuses is None:
        scan_problems.append("the privileged scan could not be read")
    else:
        for location in LOCATIONS:
            status = statuses.get(location)
            if status != "ok":
                scan_problems.append(
                    f"{location}: the scan reported {status!r}, so that location is unproven")
            for pattern in TOKEN_PATTERNS:
                entry = findings.get((location, pattern))
                if entry is None:
                    scan_problems.append(f"{location}/{pattern}: not reported")
                elif entry[0]:
                    # Location and pattern id ONLY - never what was found.
                    readable.append(f"{location}/{pattern} x{entry[1]}")
    rows.append((
        f"{GATE}.unreadable",
        "a privileged workload process finds NO real Claude OAuth token material anywhere T021 "
        f"names ({', '.join(LOCATIONS)}); only pattern ids, location ids and counts are recorded, "
        "never a value, an excerpt or a digest",
        not scan_problems and not readable,
        "; ".join(scan_problems) if scan_problems else
        (f"token material IS readable at: {', '.join(readable)}" if readable else
         f"{len(LOCATIONS)} location(s) x {len(TOKEN_PATTERNS)} pattern(s) scanned under sudo; no "
         "match anywhere"),
        ran and "scan_exit" in obs,
    ))
    secrets_readable = bool(readable) or bool(scan_problems)

    fingerprint_row, before, _ = common.fingerprint_unchanged_row(GATE, obs, work)
    rows.append((*fingerprint_row, "policy_after_exit" in obs))
    rows.append((*common.cleanup_row(GATE, obs, work, (SANDBOX,)), "ls_after_exit" in obs))

    return common.criteria_rows(rows), before, (not secrets_readable), readable


def record(obs_path, work=WORK, versions_path=VERSIONS, evidence_path=EVIDENCE):
    obs = common.read_observations(obs_path)
    versions = common.read_versions(versions_path)
    criteria, fingerprint, unreadable, readable = evaluate(obs, work, versions)
    status = "PASS" if all(row["result"] == "PASS" for row in criteria) else "FAIL"

    evidence = {
        "gate": GATE,
        "status": status,
        "run_at": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "versions": {
            "sbx": (common.read_json(work, "pf-version.json") or {}).get("client", {}).get("version")
            or None
        },
        "provenance": common.rules_module.evidence_provenance(GATE, versions),
        "criteria": criteria,
        "fallback_applied": None,
        "notes": (
            "Claude secret unreadability, probed from inside one mountless sandbox created with "
            "--skills off from the exact pinned sandbox_bases.claude base, under the network "
            "policy accepted G4 proved for claude/trusted. The scanning workload deliberately has "
            "SUDO and resolves to root, because in this architecture the agent already has sudo "
            "and full read access to the VM filesystem, so an unprivileged negative would answer "
            "the wrong question. Only pattern ids, location ids, found/not-found and match counts "
            "are recorded: never a token value, a partial value, a cookie, an Authorization "
            "header, the contents of a secret file, or a hash of a candidate - a hash of a "
            "guessable secret is itself a disclosure and of a real one a verifier. Two controls "
            "make a negative meaningful: a clearly synthetic canary with a real prefix is planted "
            "and must be DETECTED, and the agent must still authenticate and execute in the same "
            "configuration the scan runs against. A FAIL here does not make Claude unavailable; "
            "it makes Claude TRUSTED-ONLY for the later G9 and eligibility decisions, which is "
            "recorded as the typed claude_secret_isolation field rather than as prose. Every rule "
            "this gate added carried --sandbox; the global fingerprint is captured with zero "
            "sandboxes before and after; sbx policy init, sbx reset and sbx rm --all are never "
            "called. The sandbox was removed by name."
        ),
    }
    if fingerprint is not None:
        evidence["network_policy_fingerprint"] = fingerprint
    # Typed, on PASS and FAIL alike: a FAIL is a real, consumable result here (trusted-only), not
    # an absence of one.
    evidence["claude_secret_isolation"] = {
        "token_material_readable_by_privileged_workload": not unreadable,
        "locations_scanned": list(LOCATIONS),
        "readable_findings": sorted(readable),
        "implication": "eligible-for-untrusted" if unreadable else "trusted-only",
    }
    with open(evidence_path, "w", encoding="utf-8") as fh:
        fh.write(json.dumps(evidence, indent=2, ensure_ascii=False) + "\n")
    return status, criteria


def main(argv):
    args = argv[1:]
    mode = args.pop(0) if args and args[0].startswith("--") else None

    if mode == "--policy":
        which = args[0] if args else ""
        allow, deny = common.claude_policy("trusted")
        emit = {"allow": allow, "deny": deny}.get(which)
        if emit is None:
            print("usage: record.py --policy allow|deny", file=sys.stderr)
            return 2
        print(",".join(emit))
        return 0

    path = args[0] if args else None
    work = args[1] if len(args) > 1 else WORK
    if path is None or mode not in (None, "--preflight"):
        print("usage: python3 gates/G1b/record.py [--policy|--preflight] "
              "<observations.env> [<work-dir>]", file=sys.stderr)
        return 2

    if mode == "--preflight":
        obs = common.read_observations(path)
        rows = common.preflight(GATE, obs, work, common.read_versions(VERSIONS))
        for identifier, description, result, observed in rows:
            print(f"  {identifier} {'PASS' if result else 'FAIL'} {description}")
            print(f"          observed: {observed}")
        return 0 if all(row[2] for row in rows) else 1

    status, criteria = record(path, work)
    print(f"G1b: {status} ({EVIDENCE})")
    for row in criteria:
        print(f"  {row['id']:<22} {row['result']:<8} {row['description']}")
        print(f"          observed: {row['evidence_ref']}")
    return 0 if status == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
