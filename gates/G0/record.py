"""Evaluate the G0 observations and write gates/G0.json (tasks.md T008).

Standard library only. It reads what gates/G0/run.sh captured from the documented Docker
Sandboxes surfaces and decides every criterion from machine-readable JSON plus the command's
exit status, never from table output.

Shapes come from the **observed sbx v0.43.0 candidate** on this host. Until G0 passes and
runtime/versions.yaml records it, v0.43.0 is a candidate, not the accepted V1 pin. Docker
documents these commands and their --json flags; the field and check names below are
**empirically observed in v0.43.0**, not a promised stable schema. A future sbx version re-runs
G0 (research R27) and may need this parser adapted.

  sbx version --json   {"client": {"version": ...}, "server": {"state": ..., "version": ...}}
  sbx diagnose --json  {"checks": [{"name": ..., "status": ..., "message": ...}], "summary": ...}
                       observed check names used here: "Daemon", "Version match", "Authentication"
  sbx settings get --json ssh.agentForwardingEnabled
                       {"key": ..., "value": <bool>, "source": ..., "requires_restart": true}
  sbx ls --json        {"sandboxes": [...]}   (observed authenticated, empty, on this host)
  sbx policy ls --json the global network policy document, or, when it is uninitialized,
                       exit 1 with empty stdout and exactly POLICY_UNINITIALIZED_STDERR

Fail-closed everywhere: a missing file, malformed JSON, an unexpected shape or an absent field
fails its criterion instead of being read as success, and nothing reaches runtime/versions.yaml
unless every other criterion passed.

Exit status is part of the decision for the deterministic surfaces (version, ls, settings reads
and writes, daemon restart, policy init, the policy read after init, and the policy read when a
policy already exists). `sbx diagnose` is the exception: it exits non-zero when **any** check
fails, including checks outside G0's decision set (disk space, for instance), so only its named
checks — Daemon, Version match, Authentication — decide, never its exit code.

Usage:
  python3 gates/G0/record.py <observations.env> [<work-dir>]
                                    write gates/G0.json (and the pins on PASS)
  python3 gates/G0/record.py --step1 <obs> [<work-dir>]
                                    exit 0 only if step 1 holds
  python3 gates/G0/record.py --ready <obs> [<work-dir>]
                                    PRE-SSH guard: exit 0 only if step 1 holds and no sandbox
                                    is listed, so the SSH change may proceed
  python3 gates/G0/record.py --post-ssh <obs> [<work-dir>]
                                    POST-SSH guard: exit 0 only if G0.1-G0.7 pass, so the SSH
                                    change is proven effective before the policy bootstrap
  python3 gates/G0/record.py --policy-uninitialized <obs> [<work-dir>]
                                    exit 0 only for the exact uninitialized representation
                                    (the deny-all bootstrap is then needed)
"""

import datetime
import importlib.util
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
WORK = os.path.join(ROOT, "gates", "G0", "work")
VERSIONS = os.path.join(ROOT, "runtime", "versions.yaml")
EVIDENCE = os.path.join(ROOT, "gates", "G0.json")

SSH_KEY = "ssh.agentForwardingEnabled"
MINIMUM_SBX = (0, 43, 0)
# The conservative bootstrap preset. G0 initializes the global network policy only when it is
# uninitialized, because sbx requires a preset before the first sandbox runs and G6 creates
# sandboxes before G4. G4 remains authoritative for the proven effective policy.
BOOTSTRAP_PRESET = "deny-all"
# The uninitialized global network policy, exactly as the observed sbx v0.43.0 candidate
# reports it: exit 1, empty stdout, and this stderr byte for byte. Only this representation
# authorizes the deny-all bootstrap. A 401, a changed wording, a different exit code or any
# other output is not recognized, so it can never authorize a global mutation.
POLICY_UNINITIALIZED_EXIT = "1"
POLICY_UNINITIALIZED_STDERR = (
    "ERROR: global network policy has not been initialized\n"
    "\n"
    "Initialize it with:\n"
    "  sbx policy init <allow-all|balanced|deny-all>\n"
)
MISSING = object()

# The order G0 executes in. Step 1 is observed in one go; each later criterion is reached only
# when every criterion before it passed, which is what the run.sh guards enforce:
#   preconditions -> SSH mutation -> proof the SSH mutation took effect -> policy bootstrap.
STEP1 = ("G0.1", "G0.2", "G0.3", "G0.4", "G0.5")
GUARDS = {
    "--step1": STEP1,
    "--ready": STEP1 + ("G0.6",),
    "--post-ssh": STEP1 + ("G0.6", "G0.7"),
}


def load_rules():
    path = os.path.join(ROOT, "gates", "eligibility_rules.py")
    spec = importlib.util.spec_from_file_location("eligibility_rules", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def read_observations(path):
    observations = {}
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            key, sep, value = line.rstrip("\n").partition("=")
            if sep:
                observations[key] = value
    return observations


def read_json(work, name):
    """Parsed <work>/<name>, or None when it is missing or not JSON (fail-closed)."""
    try:
        with open(os.path.join(work, name), encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return None


def read_text(work, name):
    try:
        with open(os.path.join(work, name), encoding="utf-8") as fh:
            return fh.read()
    except OSError:
        return ""


def parse_version(text):
    """The leading dotted number of a version string, or None when there isn't one."""
    digits = ""
    for char in str(text).strip().lstrip("v"):
        if char.isdigit() or char == ".":
            digits += char
        elif digits:
            break
    parts = [int(p) for p in digits.split(".") if p]
    return tuple(parts) if parts else None


def diagnose_status(diagnose, name):
    """The status of one named sbx diagnose check, or None when it isn't reported."""
    if not isinstance(diagnose, dict) or not isinstance(diagnose.get("checks"), list):
        return None
    for check in diagnose["checks"]:
        if isinstance(check, dict) and check.get("name") == name:
            status = check.get("status")
            return status if isinstance(status, str) else None
    return None


def sandbox_count(listing):
    """How many sandboxes sbx ls --json reports, or None when the shape isn't one G0 accepts.

    T008 requires that no sandbox is *running* during the global setting change.

    The authenticated `sbx ls --json` of the observed v0.43.0 candidate emits exactly
    {"sandboxes": [...]}, captured read-only on this host. Only that shape is accepted:

    - a bare array, a wrapper holding some other list such as {"warnings": []} or
      {"errors": []}, and a document with any additional top-level key are all unknown. Reading
      one of those as "zero sandboxes" would treat an unrelated or changed document as proof
      that nothing is running, so they return None and block the change.
    - every listed sandbox blocks the change, running or not. The capture was empty, so no
      per-entry field has been observed; `sbx ls` documents a status column for the table but
      no JSON field name, and guessing one would be fabrication. Once an entry is observed
      with a reliable status field, this narrows to running sandboxes as T008 states.
    """
    if isinstance(listing, dict) and set(listing) == {"sandboxes"} and isinstance(listing["sandboxes"], list):
        return len(listing["sandboxes"])
    return None


def setting_value(document, key):
    """The evaluated value from sbx settings get --json, or MISSING when it isn't reported."""
    if isinstance(document, dict) and document.get("key") == key and "value" in document:
        return document["value"]
    return MISSING


def governance_state(*documents):
    """Governance as sbx reports it, or 'unknown/not observable' when it doesn't.

    `sbx policy ls` prints a Governance status line when an organization manages the policy,
    but no JSON field for it is documented and none has been observed in v0.43.0. Nothing is
    inferred: an absent field is recorded as not observable, never as "no governance".
    """
    for document in documents:
        if isinstance(document, dict) and isinstance(document.get("governance"), (str, dict, bool)):
            return str(document["governance"])
    return "unknown/not observable from sbx policy ls --json"


def policy_uninitialized(work, obs):
    """True only for the exact uninitialized representation observed from the candidate sbx.

    This is the one observation that can't require exit 0, because sbx reports the
    uninitialized state as an error. It is therefore matched exactly — exit code, empty
    stdout and the full stderr text — rather than by substring, since it is what authorizes
    the one-time global `sbx policy init`. Anything else (a 401, a reworded message, extra
    output, a different exit code) returns False and leaves the policy untouched.
    """
    return (
        obs.get("policy_before_exit") == POLICY_UNINITIALIZED_EXIT
        and read_text(work, "policy-before.json") == ""
        and read_text(work, "policy-before.err") == POLICY_UNINITIALIZED_STDERR
    )


def evaluate(obs, work=WORK):
    """Return G0's criteria. Each is PASS, FAIL or NOT-RUN, with what was observed."""
    version = read_json(work, "version.json")
    client = parse_version((version or {}).get("client", {}).get("version", ""))
    server = (version or {}).get("server", {}) if isinstance(version, dict) else {}
    server_version = parse_version(server.get("version", "")) if isinstance(server, dict) else None
    diagnose = read_json(work, "diagnose.json")
    daemon = diagnose_status(diagnose, "Daemon")
    version_match = diagnose_status(diagnose, "Version match")
    auth = diagnose_status(diagnose, "Authentication")

    rows = [
        (
            "G0.1",
            "Docker Desktop server is reachable",
            bool(obs.get("docker_server")),
            f"Docker Engine {obs.get('docker_server') or 'unreachable'}",
        ),
        (
            "G0.2",
            "sbx is installed and reports its version",
            obs.get("sbx_present") == "true" and obs.get("version_exit") == "0" and client is not None,
            f"sbx client {(version or {}).get('client', {}).get('version', 'not reported')}, "
            f"version --json exit {obs.get('version_exit', '-')}",
        ),
        (
            "G0.3",
            "sbx client and server >= 0.43.0, server running (mountless create, --skills=off)",
            obs.get("version_exit") == "0"
            and client is not None
            and client >= MINIMUM_SBX
            and server.get("state") == "running"
            and server_version is not None
            and server_version >= MINIMUM_SBX,
            f"client {client}, server state {server.get('state', 'not reported')} "
            f"version {server.get('version', 'not reported')}",
        ),
        (
            "G0.4",
            "sbx daemon healthy and matching (diagnose Daemon, Version match)",
            daemon == "pass" and version_match == "pass",
            f"diagnose Daemon: {daemon}, Version match: {version_match}",
        ),
        (
            "G0.5",
            "the developer is signed in to Docker Sandboxes (diagnose Authentication)",
            auth == "pass",
            f"diagnose Authentication: {auth}",
        ),
    ]
    step1 = all(result for _, _, result, _ in rows)

    count = sandbox_count(read_json(work, "ls.json")) if "ls_exit" in obs else None
    before = setting_value(read_json(work, "settings-before.json"), SSH_KEY)
    after = setting_value(read_json(work, "settings-after.json"), SSH_KEY)
    daemon_after = diagnose_status(read_json(work, "diagnose-after.json"), "Daemon")
    ssh_effective = (
        obs.get("settings_before_exit") == "0"
        and isinstance(before, bool)
        and obs.get("settings_set_exit") == "0"
        and obs.get("daemon_restart_exit") == "0"
        and obs.get("settings_after_exit") == "0"
        and after is False
        and daemon_after == "pass"
    )

    policy_before = read_json(work, "policy-before.json")
    policy_after = read_json(work, "policy-after.json")
    was_uninitialized = policy_uninitialized(work, obs)
    initialized_here = "policy_init_exit" in obs
    if was_uninitialized:
        policy_ok = (
            obs.get("policy_init_preset") == BOOTSTRAP_PRESET
            and obs.get("policy_init_exit") == "0"
            and obs.get("policy_after_exit") == "0"
            and policy_after is not None
        )
        policy_observed = (
            f"previous_state=uninitialized; bootstrap_preset={BOOTSTRAP_PRESET}; "
            "reason=prerequisite for pre-G4 sandbox verification gates; "
            f"init exit {obs.get('policy_init_exit', '-')}; "
            f"policy ls after init exit {obs.get('policy_after_exit', '-')}; "
            f"governance={governance_state(policy_after)}"
        )
    else:
        # An existing preset or governance state is recorded and left for G4 to evaluate.
        policy_ok = (
            obs.get("policy_before_exit") == "0"
            and policy_before is not None
            and not initialized_here
        )
        policy_observed = (
            f"previous_state=initialized; policy ls exit {obs.get('policy_before_exit', '-')}; "
            "left unchanged for G4; "
            f"governance={governance_state(policy_before, policy_after)}"
            if policy_before is not None
            else f"sbx policy ls --json exit {obs.get('policy_before_exit', '-')}: neither a "
            "policy document nor the uninitialized state"
        )

    rows += [
        (
            "G0.6",
            "no sandbox is running while the global setting changes",
            obs.get("ls_exit") == "0" and count == 0,
            f"sbx ls --json exit {obs.get('ls_exit', '-')}, reports {count} sandboxes "
            "(any listed sandbox blocks the change: no per-entry status field has been "
            "observed, so G0 uses the stricter rule)"
            if count is not None
            else f"sbx ls --json exit {obs.get('ls_exit', '-')}, not the observed "
            '{"sandboxes": [...]} shape',
        ),
        (
            "G0.7",
            f"global {SSH_KEY} is effectively false: set, daemon restarted, re-read, daemon healthy",
            ssh_effective,
            f"before={before if before is not MISSING else 'not reported'!r}, "
            f"after={after if after is not MISSING else 'not reported'!r}, "
            f"get-before exit {obs.get('settings_before_exit', '-')}, "
            f"set exit {obs.get('settings_set_exit', '-')}, "
            f"daemon restart exit {obs.get('daemon_restart_exit', '-')}, "
            f"get-after exit {obs.get('settings_after_exit', '-')}, "
            f"diagnose Daemon after restart: {daemon_after}",
        ),
        (
            "G0.8",
            "global network policy recorded; bootstrap preset only when uninitialized",
            policy_ok,
            policy_observed,
        ),
        (
            "G0.9",
            "exact sbx and Claude Code versions written into runtime/versions.yaml",
            obs.get("pins_written") == "true",
            f"sbx={obs.get('sbx_exact') or 'not written'}, "
            f"claude_code={obs.get('claude_code') if obs.get('pins_written') == 'true' else 'not written'}",
        ),
    ]

    criteria = []
    reached = step1
    for identifier, description, result, observed in rows:
        if identifier in STEP1:
            outcome = "PASS" if result else "FAIL"
        elif reached:
            outcome = "PASS" if result else "FAIL"
            if not result:
                # The gate stops here, so nothing after this was attempted.
                reached = False
        else:
            outcome = "NOT-RUN"
            observed = "an earlier step did not hold, so the gate stopped before this one (fail-closed)"
        criteria.append(
            {"id": identifier, "description": description, "result": outcome, "evidence_ref": observed}
        )
    return criteria


def write_pins(sbx_exact, claude_code_exact, versions_path=VERSIONS):
    """Write the two pins G0 owns, keeping the canonical JSON-compatible YAML form.

    sbx.minimum and claude_code.tested are never touched: they are decided values.
    """
    with open(versions_path, encoding="utf-8") as fh:
        versions = json.load(fh)
    versions["sbx"]["exact"] = sbx_exact
    versions["claude_code"]["exact"] = claude_code_exact
    with open(versions_path, "w", encoding="utf-8") as fh:
        fh.write(json.dumps(versions, indent=2, sort_keys=True, ensure_ascii=False) + "\n")
    return versions


def record(obs_path, work=WORK, versions_path=VERSIONS, evidence_path=EVIDENCE):
    """Evaluate, write the pins when everything else passed, then write the evidence."""
    rules = load_rules()
    obs = read_observations(obs_path)
    criteria = evaluate(obs, work)

    # The pins are written before the evidence, so the provenance always describes the file as
    # it then stands, and only when every other criterion passed.
    if all(row["result"] == "PASS" for row in criteria if row["id"] != "G0.9"):
        sbx_exact = (read_json(work, "version.json") or {}).get("client", {}).get("version")
        claude_code = obs.get("claude_code")
        if sbx_exact and claude_code:
            write_pins(sbx_exact, claude_code, versions_path)
            with open(obs_path, "a", encoding="utf-8") as fh:
                fh.write(f"pins_written=true\nsbx_exact={sbx_exact}\n")
            obs = read_observations(obs_path)
            criteria = evaluate(obs, work)

    status = "PASS" if all(row["result"] == "PASS" for row in criteria) else "FAIL"
    with open(versions_path, encoding="utf-8") as fh:
        versions = json.load(fh)
    evidence = {
        "gate": "G0",
        "status": status,
        "run_at": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "versions": {
            "docker_engine": obs.get("docker_server") or None,
            "docker_agent": obs.get("docker_agent") or None,
            "claude_code": obs.get("claude_code") or None,
            "git": obs.get("git") or None,
            "sbx": (read_json(work, "version.json") or {}).get("client", {}).get("version") or None,
        },
        "provenance": rules.evidence_provenance("G0", versions),
        "criteria": criteria,
        "fallback_applied": None,
        "notes": (
            "Observed by gates/G0/run.sh from the documented sbx JSON surfaces, against the "
            "observed sbx v0.43.0 candidate (it becomes the V1 pin only once G0 passes and "
            "runtime/versions.yaml records it). No credential value is read or recorded. G0 initializes the "
            f"global network policy to the conservative {BOOTSTRAP_PRESET} bootstrap preset only "
            "when sbx reports it uninitialized, because a preset is required before the first "
            "sandbox runs (G6) and G4 runs later; an existing policy or governance state is "
            "recorded and left unchanged. G4 remains authoritative for the effective policy."
        ),
    }
    if status != "PASS":
        evidence["notes"] += " Fail-closed: no pin was written for a gate that did not pass."
    with open(evidence_path, "w", encoding="utf-8") as fh:
        fh.write(json.dumps(evidence, indent=2, ensure_ascii=False) + "\n")
    return status, criteria


def main(argv):
    args = argv[1:]
    mode = args.pop(0) if args and args[0].startswith("--") else None
    path = args[0] if args else None
    work = args[1] if len(args) > 1 else WORK
    if path is None or mode not in (None, "--policy-uninitialized", *GUARDS):
        print(
            "usage: python3 gates/G0/record.py "
            "[--step1|--ready|--post-ssh|--policy-uninitialized] <observations.env> [<work-dir>]",
            file=sys.stderr,
        )
        return 2
    if mode == "--policy-uninitialized":
        return 0 if policy_uninitialized(work, read_observations(path)) else 1
    if mode in GUARDS:
        results = {row["id"]: row["result"] for row in evaluate(read_observations(path), work)}
        return 0 if all(results[identifier] == "PASS" for identifier in GUARDS[mode]) else 1

    status, criteria = record(path, work)
    print(f"G0: {status} ({EVIDENCE})")
    for row in criteria:
        print(f"  {row['id']} {row['result']:<8} {row['description']}")
        print(f"          observed: {row['evidence_ref']}")
    return 0 if status == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
