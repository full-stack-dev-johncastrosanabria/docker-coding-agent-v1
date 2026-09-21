"""Static and environment checks for `scripts/verify.sh` and `dca verify` (tasks.md T061).

Standard library only. `verify.sh` stays a POSIX entry point for the checks that are natural in
shell; everything that has to read structured data - the policy files, the gate evidence, the
version pins, the effective Codex tool lists - lives here, because expressing those in `sh` would
mean parsing JSON with `sed`, and a check nobody can read is a check nobody maintains.

EVERY CHECK NAMES ITSELF. Each failure carries the check id, so a seeded negative (a fifth skill, a
missing pin, a planted `speckit` file, a reviewer that lost its git tools, a changed network
fingerprint) fails with a name rather than a stack trace.

CHECKS THAT NEED CREDENTIALS OR A RUNNING DAEMON ARE SEPARATED, not skipped silently. `--static`
runs only the credential-free, daemon-free set (what CI can run); without it the live checks run as
well and a missing prerequisite is itself a failure. `docker agent debug toolsets` and
`debug skills` load the Codex team using the developer's existing sign-in, so they are live checks
by construction - and they never print or log a token value.

NO CHECK EVER READS A CREDENTIAL VALUE. Provider keys are tested for ABSENCE **by name only**;
login state is read from safe structural fields. A verifier that had to read a secret to confirm
the secret was absent would be the wrong shape of tool.
"""

import argparse
import hashlib
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RUNTIME = os.path.join(ROOT, "runtime")
GATES = os.path.join(ROOT, "gates")
POLICY = os.path.join(RUNTIME, "policy")

RUNTIME_SKILLS = ("change-receipt", "repository-navigation", "root-cause-debugging",
                  "verification")
BACKENDS = ("claude", "codex")
AGENTSIGNORE_HEADER = "# context hygiene only — NOT a security boundary"

#: Provider API-key variable names, checked for ABSENCE by name only. A value is never read.
PROVIDER_KEY_NAMES = ("OPENAI_API_KEY", "OPENAI_BASE_URL", "OPENAI_ORG_ID", "ANTHROPIC_API_KEY",
                      "ANTHROPIC_AUTH_TOKEN", "CLAUDE_API_KEY", "ANTHROPIC_BASE_URL")

#: The limits research R19 fixed. Restated here deliberately: `limits.yaml` is data the launcher
#: trusts, so something outside it has to say what the right values are.
EXPECTED_HOST_LIMITS = {
    "direct": {"retries": 3, "steps": 120, "tokens": 3000000, "wall_clock_seconds": 1200},
    "planned": {"retries": 5, "steps": 300, "tokens": 8000000, "wall_clock_seconds": 2700},
}
EXPECTED_CEILINGS = {"max_consecutive_tool_calls": 25, "max_iterations": 150,
                     "max_tokens": 8000000}

#: The effective Codex tool sets T055 specifies, as the pinned binary reports them.
EXPECTED_CODEX_TOOLS = {
    "root": {"create_directory", "directory_tree", "edit_file", "list_directory", "read_file",
             "read_multiple_files", "read_skill", "remove_directory", "search_files_content",
             "shell", "transfer_task", "write_file"},
    "researcher": {"directory_tree", "list_directory", "read_file", "read_multiple_files",
                   "search_files_content"},
    "reviewer": {"directory_tree", "git_diff", "git_log", "git_status", "list_directory",
                 "read_file", "read_multiple_files", "search_files_content"},
}


def _load(name, path):
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def read_json(path):
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)


def run(args, env=None):
    merged = dict(os.environ)
    merged.update(env or {})
    try:
        proc = subprocess.run(args, env=merged, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                              check=False)
    except OSError as exc:
        return 127, "", str(exc)
    return (proc.returncode, proc.stdout.decode("utf-8", "replace"),
            proc.stderr.decode("utf-8", "replace"))


class Report:
    def __init__(self):
        self.failures = []
        self.notes = []

    def fail(self, check, message):
        self.failures.append(f"{check}: {message}")

    def note(self, message):
        self.notes.append(message)

    def require(self, check, condition, message):
        if not condition:
            self.fail(check, message)
        return condition


# --- static checks -------------------------------------------------------------------------------


def check_skills(report):
    directory = os.path.join(RUNTIME, "skills")
    present = sorted(name for name in os.listdir(directory)
                     if os.path.isdir(os.path.join(directory, name)))
    if present != sorted(RUNTIME_SKILLS):
        report.fail("skills", f"runtime/skills must hold exactly {sorted(RUNTIME_SKILLS)}, "
                              f"found {present}")
    for name in present:
        if not os.path.isfile(os.path.join(directory, name, "SKILL.md")):
            report.fail("skills", f"{name} has no SKILL.md")


def check_actions(report):
    actions = read_json(os.path.join(POLICY, "actions.yaml"))
    ids = sorted(entry["id"] for entry in actions["classes"])
    if ids != list(range(1, 32)):
        report.fail("actions", f"action classes must be exactly 1-31, found {ids}")
    for entry in actions["classes"]:
        decision = entry.get("decision") or {}
        for trust in ("trusted", "untrusted"):
            if decision.get(trust) not in ("ALLOW", "ASK", "DENY"):
                report.fail("actions",
                            f"class {entry['id']} has no single {trust} decision")


def check_limits(report):
    limits = read_json(os.path.join(POLICY, "limits.yaml"))
    for classification, expected in EXPECTED_HOST_LIMITS.items():
        actual = (limits.get("host_limits") or {}).get(classification) or {}
        for field, value in expected.items():
            if actual.get(field) != value:
                report.fail("limits", f"host_limits.{classification}.{field} is "
                                      f"{actual.get(field)!r}, expected {value!r}")
    ceilings = limits.get("native_ceilings") or {}
    for field, value in EXPECTED_CEILINGS.items():
        if ceilings.get(field) != value:
            report.fail("limits", f"native_ceilings.{field} is {ceilings.get(field)!r}, "
                                  f"expected {value!r}")


def check_network(report):
    network = read_json(os.path.join(POLICY, "network.yaml"))
    proven = read_json(os.path.join(GATES, "G4.json"))["proven_network_allowset"]
    inventory = read_json(os.path.join(GATES, "inventory", "control-plane-hosts.json"))
    def _hosts(value):
        return value.get("hosts", []) if isinstance(value, dict) else (value or [])

    required = {
        backend: {
            profile: {entry["host"] for entry in _hosts(value)
                      if entry.get("sandbox_required") is True
                      and profile in (entry.get("profiles") or [])}
            for profile in ("trusted", "untrusted")
        }
        for backend, value in inventory["backends"].items()
    }
    for backend, profiles in sorted(network["backends"].items()):
        for profile, cell in sorted(profiles.items()):
            allowed = set(cell["control_plane"]) | set(cell["allow"])
            unproven = allowed - set(proven.get(backend, {}).get(profile, []))
            if unproven:
                report.fail("network", f"{backend}/{profile} allows {sorted(unproven)}, which "
                                       "gates/G4.json did not prove")
            not_required = set(cell["control_plane"]) - required.get(backend, {}).get(profile,
                                                                                      set())
            if not_required:
                report.fail("network", f"{backend}/{profile} control_plane lists "
                                       f"{sorted(not_required)}, which the inventory does not "
                                       "mark sandbox_required for that profile")
            if profile == "untrusted" and cell["allow"]:
                report.fail("network", f"{backend}/untrusted must have an empty allow list "
                                       "(grants only)")
    for host in network["must_deny"]:
        for backend, profiles in network["backends"].items():
            for profile, cell in profiles.items():
                if host in set(cell["control_plane"]) | set(cell["allow"]):
                    report.fail("network", f"{host} is must-deny but allowed for "
                                           f"{backend}/{profile}")


def check_agentsignore(report):
    path = os.path.join(ROOT, ".agentsignore")
    if not os.path.isfile(path):
        report.fail("agentsignore", ".agentsignore is missing")
        return
    with open(path, encoding="utf-8") as handle:
        first = handle.readline().rstrip("\n")
    if first != AGENTSIGNORE_HEADER:
        report.fail("agentsignore", f"first line is {first!r}, expected {AGENTSIGNORE_HEADER!r}")


def check_artifact_pin(report):
    versions = read_json(os.path.join(RUNTIME, "versions.yaml"))
    artifact = versions.get("docker_agent_artifact") or {}
    digest = artifact.get("sha256")
    if not isinstance(digest, str) or len(digest) != 64 or digest != digest.lower():
        report.fail("pins", "runtime/versions.yaml docker_agent_artifact.sha256 is missing or "
                            "not 64 lowercase hex")
    for backend in BACKENDS:
        base = ((versions.get("sandbox_bases") or {}).get(backend) or {})
        if not base.get("base") or not str(base.get("version", "")).startswith("sha256:"):
            report.fail("pins", f"sandbox_bases.{backend} has no pinned base and digest")


def check_provider_keys(report):
    """Presence by NAME only. No value is ever read, printed or recorded."""
    present = [name for name in PROVIDER_KEY_NAMES if os.environ.get(name)]
    if present:
        report.fail("provider-keys",
                    "these provider variables are set in the environment and must not be: "
                    + ", ".join(sorted(present)))


def check_eligibility(report):
    rules = _load("dca_eligibility_rules", os.path.join(GATES, "eligibility_rules.py"))
    document = read_json(os.path.join(GATES, "eligibility.json"))
    versions = read_json(os.path.join(RUNTIME, "versions.yaml"))
    for problem in rules.check(document):
        report.fail("eligibility", problem)
    for problem in rules.check_binding(document, versions):
        report.fail("eligibility", problem)


def check_no_speckit(report):
    for current, directories, files in os.walk(RUNTIME):
        directories[:] = [d for d in directories if d != "__pycache__"]
        for name in files + directories:
            if "speckit" in name.lower():
                report.fail("spec-kit-isolation",
                            f"construction skill path under runtime/: "
                            f"{os.path.relpath(os.path.join(current, name), ROOT)}")
        for name in files:
            path = os.path.join(current, name)
            try:
                with open(path, encoding="utf-8", errors="ignore") as handle:
                    if "speckit" in handle.read().lower():
                        report.fail("spec-kit-isolation",
                                    f"speckit reference in runtime file: "
                                    f"{os.path.relpath(path, ROOT)}")
            except OSError:
                continue


# --- live checks ----------------------------------------------------------------------------------


def available_backends():
    try:
        document = read_json(os.path.join(GATES, "eligibility.json"))
    except (OSError, ValueError):
        return []
    return [name for name in BACKENDS
            if (document.get("backends") or {}).get(name, {}).get("available") is True]


def check_version_pins(report, allow_drift):
    versions = read_json(os.path.join(RUNTIME, "versions.yaml"))
    observed = {}

    code, out, _ = run(["sbx", "version"])
    observed["sbx"] = out.strip()
    exact = (versions.get("sbx") or {}).get("exact")
    if code != 0:
        report.fail("pins", "sbx is not installed or did not report a version")
    elif exact and exact not in out:
        message = f"sbx reports {out.strip()!r}, pin is {exact}"
        report.fail("pins", message) if not allow_drift else report.note(f"drift: {message}")

    code, out, _ = run(["docker", "agent", "version"])
    pinned = versions.get("docker_agent")
    if code != 0:
        report.fail("pins", "docker agent is not available")
    elif pinned and pinned not in out:
        message = f"docker agent reports {out.splitlines()[0]!r}, pin is {pinned}"
        report.fail("pins", message) if not allow_drift else report.note(f"drift: {message}")

    code, out, _ = run(["claude", "--version"])
    claude_pin = (versions.get("claude_code") or {}).get("exact")
    if code == 0 and claude_pin and claude_pin not in out:
        message = f"claude reports {out.strip()!r}, pin is {claude_pin}"
        report.fail("pins", message) if not allow_drift else report.note(f"drift: {message}")


def check_network_fingerprint(report):
    """The live global policy fingerprint must still equal the one the evidence was taken under.

    Read-only: this never calls `sbx policy init`, `sbx reset` or any mutating command. It also
    requires zero sandboxes, because the pinned sbx stops enumerating the global default-deny rule
    once a sandbox-scoped rule exists, which would serialize the same global state differently.
    """
    g4 = _load("dca_g4_record", os.path.join(GATES, "G4", "record.py"))
    eligibility = read_json(os.path.join(GATES, "eligibility.json"))
    expected = eligibility.get("network_policy_fingerprint")

    code, out, err = run(["sbx", "ls", "--json"])
    if code != 0:
        report.fail("network-fingerprint", f"sbx ls failed: {err.strip()}")
        return
    sandboxes = (json.loads(out) or {}).get("sandboxes") or []
    if sandboxes:
        report.fail("network-fingerprint",
                    f"{len(sandboxes)} sandbox(es) exist, so the global fingerprint cannot be read")
        return

    code, out, err = run(["sbx", "policy", "ls", "--json"])
    if code != 0:
        report.fail("network-fingerprint", f"sbx policy ls failed: {err.strip()}")
        return
    policy = json.loads(out)
    governance = bool((policy.get("governance") or {}).get("active"))
    actual = g4.fingerprint(policy, governance)
    if actual != expected:
        report.fail("network-fingerprint",
                    f"the live global network-policy fingerprint is {actual}, but "
                    f"gates/eligibility.json recorded {expected}")


def check_login_state(report):
    """Backend sign-in, read from safe structural facts only - never from a credential value."""
    for backend in available_backends():
        if backend == "codex":
            path = os.path.expanduser("~/.config/cagent/chatgpt-auth.json")
            if not os.path.isfile(path):
                report.fail("login", "codex is available but no ChatGPT sign-in file is present")
            elif os.path.getsize(path) == 0:
                report.fail("login", "the ChatGPT sign-in file is empty")
        else:
            code, _, _ = run(["claude", "--version"])
            if code != 0:
                report.fail("login", "claude is available but the CLI did not run")


def check_runtime_configs(report, backends, kit):
    for backend in backends:
        config = os.path.join(kit, "agents", f"{backend}.yaml")
        if not os.path.isfile(config):
            report.fail("configs", f"{backend}.yaml was not staged into the kit")
            continue
        code, out, err = run(["docker", "agent", "debug", "config", config],
                             env={"DOCKER_AGENT_KIT_DIR": kit})
        if code != 0:
            report.fail("configs", f"docker agent debug config rejected {backend}.yaml: "
                                   f"{(err or out).strip().splitlines()[-1:] or ''}")


def check_codex_capabilities(report, kit):
    config = os.path.join(kit, "agents", "codex.yaml")
    code, out, err = run(["docker", "agent", "debug", "toolsets", "--json", config],
                         env={"DOCKER_AGENT_KIT_DIR": kit})
    if code != 0:
        report.fail("codex-toolsets", f"docker agent debug toolsets failed: {err.strip()[:200]}")
        return
    try:
        rows = json.loads(out)
    except ValueError:
        report.fail("codex-toolsets", "docker agent debug toolsets did not return JSON")
        return
    effective = {row["agent"]: {tool["name"] for tool in row["tools"]} for row in rows}
    for agent, expected in sorted(EXPECTED_CODEX_TOOLS.items()):
        actual = effective.get(agent)
        if actual is None:
            report.fail("codex-toolsets", f"{agent} was not reported")
        elif actual != expected:
            report.fail("codex-toolsets",
                        f"{agent} effective tools are {sorted(actual)}, expected "
                        f"{sorted(expected)}")


def check_codex_skill_source(report, kit):
    config = os.path.join(kit, "agents", "codex.yaml")
    code, out, err = run(["docker", "agent", "debug", "skills", config],
                         env={"DOCKER_AGENT_KIT_DIR": kit})
    if code != 0:
        report.fail("codex-skills", f"docker agent debug skills failed: {err.strip()[:200]}")
        return
    listed = sorted(line.strip().split(" ", 2)[1] for line in out.splitlines()
                    if line.strip().startswith("+ "))
    if listed != sorted(RUNTIME_SKILLS):
        report.fail("codex-skills", f"listed skills are {listed}, expected "
                                    f"{sorted(RUNTIME_SKILLS)}")
    manifest = read_json(os.path.join(kit, "kit-manifest.json"))
    if sorted(manifest["skills"]) != sorted(RUNTIME_SKILLS):
        report.fail("codex-skills", "the staged kit manifest does not list exactly four skills")
    for name, entry in sorted(manifest["skills"].items()):
        path = os.path.join(kit, entry["path"])
        with open(path, "rb") as handle:
            digest = hashlib.sha256(handle.read()).hexdigest()
        if digest != entry["sha256"]:
            report.fail("codex-skills", f"{entry['path']} does not match its manifest entry")
    extras = []
    for current, _, files in os.walk(os.path.join(kit, "skills")):
        for name in files:
            relative = os.path.relpath(os.path.join(current, name), kit)
            if relative not in {entry["path"] for entry in manifest["skills"].values()}:
                extras.append(relative)
    if extras:
        report.fail("codex-skills", f"unlisted files under the trusted skill root: {extras}")


def run_test_modules(report, modules):
    code, out, err = run([sys.executable, "-m", "unittest", *modules])
    if code != 0:
        tail = (err or out).strip().splitlines()[-6:]
        report.fail("tests", "; ".join(tail) or "the contract tests failed")


# --- entry point ------------------------------------------------------------------------------------


def main(argv=None):
    parser = argparse.ArgumentParser(description="static and live checks for dca verify")
    parser.add_argument("--static", action="store_true",
                        help="run only the credential-free, daemon-free checks")
    parser.add_argument("--allow-drift", action="store_true",
                        help="record a version-pin mismatch as a note instead of a failure")
    options = parser.parse_args(argv)

    report = Report()
    check_skills(report)
    check_actions(report)
    check_limits(report)
    check_network(report)
    check_agentsignore(report)
    check_artifact_pin(report)
    check_provider_keys(report)
    check_eligibility(report)
    check_no_speckit(report)
    run_test_modules(report, ["tests.contract.test_runtime_assets",
                              "tests.contract.test_runtime_configs",
                              "tests.contract.test_backend_parity"])

    if not options.static:
        backends = available_backends()
        check_version_pins(report, options.allow_drift)
        check_network_fingerprint(report)
        check_login_state(report)
        kit_root = tempfile.mkdtemp(prefix="dca-verify-kit-")
        try:
            stage = _load("dca_stage", os.path.join(RUNTIME, "sandbox", "kit", "stage.py"))
            kit = stage.stage(kit_root, skills_source=os.path.join(RUNTIME, "skills"),
                              backends=backends)
            check_runtime_configs(report, backends, kit)
            if "codex" in backends:
                check_codex_capabilities(report, kit)
                check_codex_skill_source(report, kit)
            else:
                report.note("codex: NOT-APPLICABLE (backend unavailable per eligibility)")
            if "claude" not in backends:
                report.note("claude: NOT-APPLICABLE (backend unavailable per eligibility)")
        finally:
            shutil.rmtree(kit_root, ignore_errors=True)

    for note in report.notes:
        print(f"verify: NOTE {note}")
    for failure in report.failures:
        print(f"verify: FAIL {failure}", file=sys.stderr)
    if report.failures:
        print(f"verify: FAILED ({len(report.failures)} check(s))", file=sys.stderr)
        return 1
    scope = "static" if options.static else "static + live"
    print(f"verify: OK structured checks pass ({scope})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
