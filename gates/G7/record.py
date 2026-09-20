"""Evaluate the G7 observations and write gates/G7.json (tasks.md T010).

Standard library only. G7 changes nothing: it verifies that no SSH agent is reachable inside a
sandbox, and that the read-only detector in gates/preflight.py refuses both states in which one
would be. The negative cases come from recorded documents under gates/G7/recorded/, so no
global setting is touched.

Fail-closed: a missing file, malformed JSON, an absent field or an unsuccessful command fails
its criterion, and an unreadable setting counts as a refusal, never as "no agent".

Usage:
  python3 gates/G7/record.py <observations.env> [<work-dir>]   write gates/G7.json
  python3 gates/G7/record.py --preflight <obs> [<work-dir>]    exit 0 only if the post-G0 state
                                                               holds and no agent would forward
"""

import datetime
import importlib.util
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
WORK = os.path.join(ROOT, "gates", "G7", "work")
RECORDED = os.path.join(ROOT, "gates", "G7", "recorded")
VERSIONS = os.path.join(ROOT, "runtime", "versions.yaml")
EVIDENCE = os.path.join(ROOT, "gates", "G7.json")

SANDBOX = "dca-g7-ssh"
PREFLIGHT = ("G7.0a", "G7.0b", "G7.0c")
RECORDED_CASES = ("forwarding-enabled", "fixed-socket")


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


def read_env(work, name):
    try:
        with open(os.path.join(work, name), encoding="utf-8") as fh:
            text = fh.read()
    except OSError:
        return {}
    out = {}
    for line in text.splitlines():
        key, sep, value = line.partition("=")
        if sep:
            out[key.strip()] = value.strip()
    return out


def preflight(obs, work):
    """Read-only recheck of the post-G0 state, plus the live SSH detector."""
    with open(VERSIONS, encoding="utf-8") as fh:
        pins = json.load(fh)
    version = read_json(work, "pf-version.json")
    forwarding = read_json(work, "pf-ssh-forwarding.json")
    socket_path = read_json(work, "pf-ssh-socket.json")
    policy = read_json(work, "pf-policy.json")
    refusals = preflight_module.ssh_forwarding_refusals(forwarding, socket_path)

    return [
        (
            "G7.0a",
            "sbx client and server still match the pinned exact version and the server is running",
            obs.get("pf_version_exit") == "0"
            and preflight_module.version_pins_hold(version, pins["sbx"]["exact"]),
            f"pin {pins['sbx']['exact']}, client "
            f"{(version or {}).get('client', {}).get('version')}, server "
            f"{(version or {}).get('server', {}).get('version')} state "
            f"{(version or {}).get('server', {}).get('state')}, exit {obs.get('pf_version_exit', '-')}",
        ),
        (
            "G7.0b",
            "no SSH agent would reach a sandbox: forwarding disabled and no fixed agent socket",
            obs.get("pf_ssh_forwarding_exit") == "0"
            and obs.get("pf_ssh_socket_exit") == "0"
            and refusals == [],
            f"{preflight_module.SSH_FORWARDING_KEY}="
            f"{preflight_module.setting_value(forwarding, preflight_module.SSH_FORWARDING_KEY)!r}, "
            f"{preflight_module.SSH_SOCKET_KEY}="
            f"{preflight_module.setting_value(socket_path, preflight_module.SSH_SOCKET_KEY)!r}, "
            f"detector refusals {refusals or 'none'}",
        ),
        (
            "G7.0c",
            "global network policy is still the post-G0 bootstrap baseline (unrepaired)",
            obs.get("pf_policy_exit") == "0"
            and policy is not None
            and preflight_module.network_baseline_holds(policy),
            f"exit {obs.get('pf_policy_exit', '-')}, "
            f"{len(preflight_module.network_rules(policy))} network rule(s) "
            f"{[{k: r.get(k) for k in preflight_module.BOOTSTRAP_RULE} for r in preflight_module.network_rules(policy)]}",
        ),
    ]


def recorded_refusals(recorded=RECORDED):
    """The detector's verdict for each recorded negative state."""
    out = {}
    for case in RECORDED_CASES:
        document = read_json(recorded, f"{case}.json") or {}
        out[case] = preflight_module.ssh_forwarding_refusals(
            document.get("forwarding"), document.get("socket_path")
        )
    return out


def evaluate(obs, work, recorded=RECORDED):
    rows = [(i, d, r, o, True) for i, d, r, o in preflight(obs, work)]
    ready = all(row[2] for row in rows)

    with open(VERSIONS, encoding="utf-8") as fh:
        pins = json.load(fh)
    probe = read_env(work, f"probe-{SANDBOX}.env")
    created = obs.get(f"create_{SANDBOX}_exit") == "0"
    probed = obs.get(f"probe_{SANDBOX}_exit") == "0"
    image = obs.get(f"image_{SANDBOX}") or ""
    workspace_line = obs.get(f"workspace_line_{SANDBOX}", "")
    verdicts = recorded_refusals(recorded)

    rows.append(
        (
            "G7.1",
            "every sbx call ran with SSH_AUTH_SOCK removed from its environment",
            obs.get("sbx_env_ssh_auth_sock") == "removed",
            f"host SSH_AUTH_SOCK set: {obs.get('host_ssh_auth_sock_set', '-')}; "
            f"as seen by an sbx call: {obs.get('sbx_env_ssh_auth_sock', '-')}",
            True,
        )
    )
    pinned_base = pins["sandbox_bases"]["claude"]["base"]
    pinned_digest = pins["sandbox_bases"]["claude"]["version"]
    repository, _, tag = (pinned_base or "").rpartition(":")
    cached = preflight_module.template_image(read_json(work, "templates.json"), repository, tag)
    base_identity = (
        image == pinned_base
        and obs.get("template_ls_exit") == "0"
        and preflight_module.template_identity_matches(cached, pinned_base, pinned_digest)
    )
    rows.append(
        (
            "G7.2",
            "mountless sandbox from the exact pinned Claude base (repository, tag and digest), skills off",
            created and probed and "no workspace bind mount" in workspace_line and base_identity,
            f"create exit {obs.get(f'create_{SANDBOX}_exit', '-')}, probe exit "
            f"{obs.get(f'probe_{SANDBOX}_exit', '-')}, sbx resolved {image or 'not reported'} "
            f"(pin {pinned_base}), cached template repository "
            f"{(cached or {}).get('repository', 'not found')} tag {(cached or {}).get('tag', '-')} "
            f"image id {(cached or {}).get('id', '-')} (a short image id, not a digest on its "
            f"own) prefixing the pinned digest {pinned_digest}, workspace "
            f"'{workspace_line or 'not reported'}'",
            ready,
        )
    )
    isolation = preflight_module.ssh_agent_isolation_failures(probe)
    rows.append(
        (
            "G7.3",
            "no usable forwarded SSH-agent endpoint exists and no host SSH agent is reachable",
            probed and isolation == [],
            f"SSH_AGENT_PID {probe.get('ssh_agent_pid', '-')}; SSH_AUTH_SOCK "
            f"{probe.get('ssh_auth_sock', '-')}"
            + (
                f" ({probe.get('ssh_auth_sock_path', '-')}: exists "
                f"{probe.get('ssh_auth_sock_exists', '-')}, is socket "
                f"{probe.get('ssh_auth_sock_is_socket', '-')}, also in PID 1 env "
                f"{probe.get('pid1_has_ssh_auth_sock', '-')})"
                if probe.get("ssh_auth_sock") == "set"
                else ""
            )
            + f"; ssh-add cannot connect {probe.get('ssh_add_cannot_connect', '-')}; candidate "
            f"agent sockets {probe.get('agent_sockets', '-')}; SSH_* variables "
            f"{probe.get('ssh_env_names', '-')}; failures {isolation or 'none'}",
            probed,
        )
    )
    rows.append(
        (
            "G7.4",
            "inside the sandbox: ssh-add cannot connect to any agent",
            probed
            and probe.get("ssh_add_present") == "yes"
            and probe.get("ssh_add_exit") not in (None, "0")
            and probe.get("ssh_add_cannot_connect") == "yes"
            and probe.get("ssh_add_reports_identities") == "no",
            f"ssh-add present {probe.get('ssh_add_present', '-')}, exit "
            f"{probe.get('ssh_add_exit', '-')}, cannot connect "
            f"{probe.get('ssh_add_cannot_connect', '-')}, reports identities "
            f"{probe.get('ssh_add_reports_identities', '-')} (an agent answering with no "
            "identities loaded would be a reachable agent)",
            probed,
        )
    )
    rows.append(
        (
            "G7.5",
            "inside the sandbox: no forwarded agent socket exists",
            probed and probe.get("agent_sockets") == "0",
            f"{probe.get('agent_sockets', '-')} ssh/agent sockets under /tmp, /run, /var/run "
            f"{('(' + probe.get('socket_paths', '') + ')') if probe.get('socket_paths') else ''}",
            probed,
        )
    )
    rows.append(
        (
            "G7.6",
            "the detector refuses both recorded negative states, with no setting changed",
            all(verdicts.get(case) for case in RECORDED_CASES),
            "; ".join(f"{case}: {verdicts.get(case) or 'NOT REFUSED'}" for case in RECORDED_CASES)
            + " (recorded documents only; gates/G7/recorded/)",
            True,
        )
    )
    rows.append(
        (
            "G7.7",
            "the probe sandbox was removed by name and none remains",
            obs.get(f"rm_{SANDBOX}_exit") == "0"
            and isinstance(read_json(work, "ls-after.json"), dict)
            and not [
                e
                for e in (read_json(work, "ls-after.json") or {}).get("sandboxes", [])
                if isinstance(e, dict) and e.get("name") == SANDBOX
            ],
            f"sbx rm --force {SANDBOX} exit {obs.get(f'rm_{SANDBOX}_exit', '-')}, "
            f"{len((read_json(work, 'ls-after.json') or {}).get('sandboxes', []))} sandboxes remain",
            f"rm_{SANDBOX}_exit" in obs,
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


def record(obs_path, work=WORK, versions_path=VERSIONS, evidence_path=EVIDENCE, recorded=RECORDED):
    obs = read_observations(obs_path)
    criteria = evaluate(obs, work, recorded)
    status = "PASS" if all(row["result"] == "PASS" for row in criteria) else "FAIL"
    with open(versions_path, encoding="utf-8") as fh:
        versions = json.load(fh)
    evidence = {
        "gate": "G7",
        "status": status,
        "run_at": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "versions": {
            "sbx": (read_json(work, "pf-version.json") or {}).get("client", {}).get("version") or None
        },
        "provenance": rules_module.evidence_provenance("G7", versions),
        "criteria": criteria,
        "fallback_applied": None,
        "notes": (
            "Verification only: G7 changes nothing. The invariant it proves is that no usable "
            "forwarded SSH-agent endpoint exists and no host SSH agent is reachable. The sandbox "
            "runtime injects SSH_AUTH_SOCK=/run/ssh-agent.sock, the fixed in-VM path where its "
            "relay socket appears when forwarding is enabled; the pinned base image declares no "
            "SSH variable. A dangling value is accepted only with all corroborating evidence: "
            "SSH_AGENT_PID unset, the path neither existing nor being a socket, ssh-add unable "
            "to connect, and no candidate agent socket. G0 disabled SSH agent forwarding; this gate "
            "ran every sbx call with SSH_AUTH_SOCK removed, created one mountless sandbox with "
            "--skills off from the pinned Claude base, and probed it as the agent user. The two "
            "negative detector cases come from recorded documents under gates/G7/recorded/, so "
            "no global setting was switched. The probe records booleans, exit codes, counts and "
            "paths only, never key material, and the sandbox was removed by name; sbx rm --all "
            "is never used."
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
        print("usage: python3 gates/G7/record.py [--preflight] <observations.env> [<work-dir>]",
              file=sys.stderr)
        return 2
    if mode == "--preflight":
        rows = preflight(read_observations(path), work)
        for identifier, description, result, observed in rows:
            print(f"  {identifier} {'PASS' if result else 'FAIL'} {description}")
            print(f"          observed: {observed}")
        return 0 if all(result for _, _, result, _ in rows) else 1

    status, criteria = record(path, work)
    print(f"G7: {status} ({EVIDENCE})")
    for row in criteria:
        print(f"  {row['id']:<8} {row['result']:<8} {row['description']}")
        print(f"          observed: {row['evidence_ref']}")
    return 0 if status == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
