"""T062 production conformance observations on the final sandbox kit.

This first pass records the real, per-profile sandbox foundation. It deliberately does not
publish production-conformance.json: the agent-behaviour probes in tasks.md T062 must also pass
before that evidence can be written as PASS.
"""

import argparse
import datetime
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import sys
import tempfile


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "runtime" / "sandbox" / "kit"))

from dca.sbx import Sbx, SbxError  # noqa: E402
from scripts.verify_checks import EXPECTED_CODEX_TOOLS  # noqa: E402
import stage  # noqa: E402


def load_g4():
    spec = importlib.util.spec_from_file_location("dca_g4_record", ROOT / "gates" / "G4" / "record.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


G4 = load_g4()
WORK = ROOT / "gates" / "production" / "work"
BACKENDS = ("claude", "codex")
PROFILES = ("trusted", "untrusted")
TEMPLATES = {"claude": "claude", "codex": "docker-agent"}


def checked(rowset, identifier, condition, detail):
    row = {"id": identifier, "result": "PASS" if condition else "FAIL", "detail": detail}
    rowset.append(row)
    return condition


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def policy_check(sbx, name, host):
    status, output, error = sbx.run(
        "policy", "check", "network", host + ":443", "--sandbox", name, "--json",
        check=False)
    try:
        document = json.loads(output)
    except ValueError:
        document = None
    return {"exit": status, "decision": document, "error": error.strip()[:200]}


def run_cell(backend, profile, sbx, versions, network):
    name = f"dca-t062-{backend}-{profile}"
    rows = []
    kit = WORK / f"kit-{backend}-{profile}"
    stage.build_sbx_kit(kit, backends=[backend])
    pinned = versions["sandbox_bases"][backend]
    cell = network["backends"][backend][profile]
    allow = list(dict.fromkeys(cell["control_plane"] + cell["allow"]))
    deny = list(cell["deny"])
    created = False
    try:
        create_status, output, create_error = sbx.run(
            "create", TEMPLATES[backend], "--name", name, "--skills", "off", "--kit", str(kit),
            check=False)
        (WORK / f"create-{name}.txt").write_text(
            output + "\nSTDERR:\n" + create_error, encoding="utf-8")
        created = any(item.get("name") == name for item in sbx.list_sandboxes())
        if create_status != 0:
            raise RuntimeError(f"sbx create exited {create_status}; see {WORK / f'create-{name}.txt'}")
        checked(rows, "base.reference", pinned["base"] in output,
                {"pin": pinned["base"], "observed": re.findall(r"^\s*image\s+(.+)$", output, re.M)})
        templates = sbx.templates()
        checked(rows, "base.digest", pinned["version"][7:19] in json.dumps(templates),
                {"pin": pinned["version"], "cached_image_match": pinned["version"][7:19] in json.dumps(templates)})
        sbx.allow_network(name, allow)
        sbx.deny_network(name, deny)
        if backend == "codex" and profile == "trusted":
            credential = Path.home() / ".config/cagent/chatgpt-auth.json"
            if not credential.is_file() or credential.stat().st_size == 0:
                raise RuntimeError("the trusted ChatGPT credential file is absent or empty")
            staging = Path(tempfile.mkdtemp(prefix="dca-t062-cred-"))
            try:
                staging.chmod(0o700)
                staged = staging / "chatgpt-auth.json"
                shutil.copyfile(credential, staged)
                staged.chmod(0o600)
                sbx.copy_in(staging, name, "/run/dca/cagent")
                sbx.execute(name, "sudo chown -R \"$(id -un):$(id -gn)\" "
                            "/run/dca/cagent && "
                            "chmod 700 /run/dca/cagent && "
                            "chmod 600 /run/dca/cagent/chatgpt-auth.json")
            finally:
                shutil.rmtree(staging, ignore_errors=True)
            checked(rows, "codex.trusted_token_file", True,
                    "only chatgpt-auth.json was copied from an owner-only temporary directory")

        decisions = {host: policy_check(sbx, name, host) for host in
                     sorted(set(allow + deny + network["must_deny"] + ["example.invalid"]))}
        for host in allow:
            checked(rows, "network.allow." + host, decisions[host]["exit"] == 0, decisions[host])
        for host in sorted(set(deny + network["must_deny"] + ["example.invalid"])):
            checked(rows, "network.deny." + host, decisions[host]["exit"] == 1, decisions[host])
        status, policy_log, error = sbx.run("policy", "log", name, "--json", check=False)
        checked(rows, "network.log", status == 0,
                {"exit": status, "entries": len(policy_log.splitlines()), "error": error.strip()[:200]})

        probe = """set -eu
printf 'ssh_env=%s\\n' "${SSH_AUTH_SOCK:-}"
printf 'ssh_env_socket=%s\\n' "$(if [ -S "${SSH_AUTH_SOCK:-/nonexistent}" ]; then echo yes; else echo no; fi)"
printf 'ssh_agent_pid=%s\\n' "$(if [ -n "${SSH_AGENT_PID:-}" ]; then echo set; else echo unset; fi)"
printf 'ssh_socket_count=%s\\n' "$(find /run /tmp -type s -name '*ssh*' 2>/dev/null | wc -l | tr -d ' ')"
ssh_add_status=0
ssh_add_output=$(ssh-add -l 2>&1) || ssh_add_status=$?
printf 'ssh_add_exit=%s\\n' "$ssh_add_status"
printf 'ssh_add_cannot_connect=%s\\n' "$(printf '%s' "$ssh_add_output" | grep -qiE 'could not open a connection|error connecting' && echo yes || echo no)"
printf 'skills=%s\\n' "$(find /opt/dca/skills -mindepth 1 -maxdepth 1 -type d -print | sed 's@.*/@@' | sort | tr '\\n' ' ')"
printf 'skill_mounts=%s\\n' "$(awk '$2 ~ /[Ss]kill/ {n++} END {print n+0}' /proc/mounts)"
printf 'manifest=%s\\n' "$(sha256sum /opt/dca/kit-manifest.json | cut -d' ' -f1)"
/usr/bin/python3 -I /opt/dca/lib/dca/kit_preflight.py /opt/dca {config}
""".replace("{config}", "/home/agent/.config/cagent/config.yaml"
            if backend == "codex" else "/dev/null")
        code, output, error, timed_out = sbx.execute(name, probe, check=False, timeout=90)
        observed = dict(line.split("=", 1) for line in output.splitlines() if "=" in line)
        checked(rows, "kit.preflight", code == 0 and not timed_out and "preflight: OK" in output,
                {"exit": code, "timed_out": timed_out, "error": error.strip()[:300]})
        checked(rows, "ssh.isolation", observed.get("ssh_env_socket") == "no" and
                observed.get("ssh_socket_count") == "0" and
                observed.get("ssh_agent_pid") == "unset" and
                observed.get("ssh_add_cannot_connect") == "yes" and
                observed.get("ssh_add_exit") not in (None, "0"),
                {key: observed.get(key) for key in ("ssh_env", "ssh_env_socket",
                      "ssh_socket_count", "ssh_agent_pid", "ssh_add_exit",
                      "ssh_add_cannot_connect")})
        expected = sorted(stage.RUNTIME_SKILLS)
        actual = sorted((observed.get("skills") or "").split())
        checked(rows, "skills.exact_four", actual == expected and
                observed.get("skill_mounts") == "0",
                {"expected": expected, "actual": actual, "mounts": observed.get("skill_mounts")})
        checked(rows, "kit.manifest_present", bool(re.fullmatch(r"[0-9a-f]{64}", observed.get("manifest", ""))),
                {"sha256": observed.get("manifest")})

        if backend == "codex":
            prefix = "export DOCKER_AGENT_KIT_DIR=/opt/dca; "
            config_flag = " --config-dir /run/dca/cagent" if profile == "trusted" else ""
            if profile == "untrusted":
                # No real credential is ever provisioned to an untrusted sandbox. Debug only
                # checks that the configured tools resolve; this synthetic value cannot sign in.
                prefix = ("export DOCKER_AGENT_KIT_DIR=/opt/dca "
                          "CHATGPT_OAUTH_TOKEN=dca-synthetic-debug-only; ")
            _, env_out, _, _ = sbx.execute(
                name, "printf '%s' \"${DOCKER_AGENT_AUTO_UPDATE:-<unset>}\"", check=False)
            command = (prefix + "/opt/dca/bin/docker-agent debug toolsets --json" + config_flag + " "
                       "/opt/dca/agents/codex.yaml")
            code, out, err, timed_out = sbx.execute(name, command, check=False, timeout=90)
            try:
                toolsets = json.loads(out)
                effective = {entry["agent"]: {tool["name"] for tool in entry["tools"]}
                             for entry in toolsets}
            except (ValueError, KeyError, TypeError):
                effective = {}
            checked(rows, "codex.effective_tools",
                    code == 0 and not timed_out and effective == EXPECTED_CODEX_TOOLS,
                    {"exit": code, "actual": {key: sorted(value) for key, value in effective.items()},
                     "auto_update_env": env_out, "error": err.strip()[:1200]})
            if code != 0:
                diagnostic = ("export DOCKER_AGENT_AUTO_UPDATE=0 "
                              "DOCKER_AGENT_KIT_DIR=/opt/dca; "
                              "/opt/dca/bin/docker-agent debug toolsets --json" + config_flag + " "
                              "/opt/dca/agents/codex.yaml")
                diag_code, diag_out, diag_err, _ = sbx.execute(
                    name, diagnostic, check=False, timeout=90)
                checked(rows, "codex.update_disabled_diagnostic", diag_code == 0,
                        {"exit": diag_code, "output_bytes": len(diag_out),
                         "error": diag_err.strip()[:1200]})
            command = (prefix + "/opt/dca/bin/docker-agent debug skills" + config_flag + " "
                       "/opt/dca/agents/codex.yaml")
            code, out, err, timed_out = sbx.execute(name, command, check=False, timeout=90)
            listed = sorted(line.strip().split(" ", 2)[1] for line in out.splitlines()
                            if line.strip().startswith("+ "))
            checked(rows, "codex.skill_set",
                    code == 0 and not timed_out and listed == sorted(stage.RUNTIME_SKILLS),
                    {"exit": code, "listed": listed, "error": err.strip()[:200]})
    except (SbxError, OSError, ValueError, RuntimeError) as exc:
        checked(rows, "sandbox.execution", False, str(exc))
    finally:
        if created:
            status, error = sbx.remove(name)
            checked(rows, "cleanup", status == 0, {"exit": status, "error": error.strip()[:200]})
    return {"backend": backend, "profile": profile,
            "status": "PASS" if rows and all(row["result"] == "PASS" for row in rows) else "FAIL",
            "checks": rows}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--backend", choices=BACKENDS)
    parser.add_argument("--profile", choices=PROFILES)
    args = parser.parse_args()
    WORK.mkdir(parents=True, exist_ok=True)
    versions = read_json(ROOT / "runtime" / "versions.yaml")
    network = read_json(ROOT / "runtime" / "policy" / "network.yaml")
    eligibility = read_json(ROOT / "gates" / "eligibility.json")
    expected = read_json(ROOT / "gates" / "G4.json")["network_policy_fingerprint"]
    sbx = Sbx()
    if sbx.list_sandboxes():
        raise SystemExit("T062 requires zero sandboxes before it begins")
    actual = G4.fingerprint(sbx.policy(), bool((sbx.policy().get("governance") or {}).get("active")))
    if actual != expected:
        raise SystemExit(f"G4 global policy fingerprint changed: {actual} != {expected}")
    selected = [(backend, profile) for backend in BACKENDS for profile in PROFILES
                if (not args.backend or backend == args.backend)
                and (not args.profile or profile == args.profile)
                and eligibility["backends"][backend]["available"]]
    # Cells MERGE by (backend, profile) so a re-run of one cell does not discard the others, and
    # every cell carries its own run_at: a partial re-run must never make an older cell look as
    # though it was observed just now.
    previous = {}
    if (WORK / "foundation.json").is_file():
        for cell in read_json(WORK / "foundation.json").get("cells", []):
            previous[(cell.get("backend"), cell.get("profile"))] = cell
    output = {"run_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
              "global_policy_fingerprint": actual, "cells": []}
    for backend, profile in selected:
        cell = run_cell(backend, profile, sbx, versions, network)
        cell["run_at"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
        previous[(backend, profile)] = cell
        output["cells"] = [previous[key] for key in sorted(previous, key=lambda k: (k[0], k[1]))]
        (WORK / "foundation.json").write_text(json.dumps(output, indent=2) + "\n", encoding="utf-8")
        print(f"{backend}/{profile}: {cell['status']} ({len(cell['checks'])} checks)", flush=True)
    remaining = sbx.list_sandboxes()
    if remaining:
        raise SystemExit(f"T062 did not clean up every sandbox: {remaining}")
    print(f"T062 foundation: {len(output['cells'])} cells; zero sandboxes")
    return 0 if all(cell["status"] == "PASS" for cell in output["cells"]) else 1


if __name__ == "__main__":
    sys.exit(main())
