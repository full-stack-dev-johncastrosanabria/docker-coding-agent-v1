"""Kit staging tool (tasks.md T032, extended to the production kit by T056).

Standard library only. Lays out the whole sandbox kit under a prefix and writes the canonical kit
manifest. T055, T056, T061, T062 and the tests all use THIS tool, so what the tests exercise is
what ships: a hand-built approximation in a test would prove nothing about production.

DETERMINISM IS A REQUIREMENT, NOT A NICETY. The manifest records a SHA-256 per skill and the gate
refuses any skill whose bytes do not match, so two staging runs from the same source must produce
identical bytes or the kit would fail its own verification. Hence canonical JSON - sorted keys,
compact separators, no trailing newline - and byte copies rather than re-serialization.

IT REFUSES ANYTHING BUT THE FOUR RUNTIME SKILLS. An extra directory under the skill source would
become an unlisted entry under the trusted skill root, which the gate treats as an invalid manifest
and which then denies every skill load. Better to fail at staging, where the message is clear.

BACKEND MATERIAL IS STAGED ONLY FOR AVAILABLE BACKENDS. `gates/eligibility.json` decides, and the
kit never requires an unavailable backend's assets: one backend failing its gates must not stop the
other from running.

THE CODEX CONFIG AND ITS INSTRUCTIONS ARE STAGED TOGETHER, at `<KIT_DIR>/agents/`. The pinned v15
parser refuses an absolute or escaping `instruction_file`, so `codex.yaml` names
`instructions/<role>.md` relative to itself, and that staged pair is also what
`docker agent debug config|toolsets|skills` is run against - the validated config is the shipped
one.
"""

import hashlib
import json
import os
import shutil
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(HERE)))

TEMPLATE = os.path.join(HERE, "templates", "dca-gate.sh.in")
FINGERPRINT_TEMPLATE = os.path.join(HERE, "templates", "dca-fingerprint.sh.in")
SRC = os.path.join(ROOT, "src", "dca")
POLICY_SRC = os.path.join(ROOT, "runtime", "policy")
INSTRUCTIONS_SRC = os.path.join(ROOT, "runtime", "instructions")
AGENTS_SRC = os.path.join(ROOT, "runtime", "agents")
CLAUDE_SRC = os.path.join(ROOT, "runtime", "claude")
VERSIONS = os.path.join(ROOT, "runtime", "versions.yaml")
ELIGIBILITY = os.path.join(ROOT, "gates", "eligibility.json")

RUNTIME_SKILLS = ("repository-navigation", "root-cause-debugging", "verification",
                  "change-receipt")
INSTRUCTIONS = ("root.md", "researcher.md", "reviewer.md")
SUBAGENTS = ("dca-researcher.md", "dca-reviewer.md")
BACKENDS = ("claude", "codex")

# The contract's production values. stage.py renders these unless a caller overrides them.
PRODUCTION_PREFIX = "/"
PRODUCTION_PYTHON = "/usr/bin/python3"

GATE_MODULES = ("policy_gate.py", "shellparse.py", "fingerprint.py", "fingerprint_hook.py",
                "kit_preflight.py")
POLICY_FILES = ("actions.yaml", "network.yaml", "limits.yaml")

#: Where the kit installs Claude's managed configuration inside the VM. G1c and G1d proved that
#: Claude loads the managed copies from here in preference to a hostile repository's same-named
#: files, and that the workload user cannot write them.
CLAUDE_MANAGED_ROOT = "/etc/claude-code"


class StagingError(Exception):
    """The source tree is not something that may be staged."""


def _render_wrapper(template_path, prefix, python):
    with open(template_path, encoding="utf-8") as fh:
        template = fh.read()
    # Only these two placeholders are ever substituted, so the shipped wrapper and the tested one
    # differ in nothing else.
    return template.replace("{PREFIX}", prefix).replace("{PYTHON}", python)


def _check_skills(skills_source):
    if not os.path.isdir(skills_source):
        raise StagingError(f"skill source {skills_source} is not a directory")
    present = sorted(name for name in os.listdir(skills_source)
                     if not name.startswith(".") and
                     os.path.isdir(os.path.join(skills_source, name)))
    expected = sorted(RUNTIME_SKILLS)
    if present != expected:
        raise StagingError(
            f"skill source must hold exactly {expected}, found {present}")
    for name in expected:
        path = os.path.join(skills_source, name, "SKILL.md")
        if not os.path.isfile(path):
            raise StagingError(f"skill {name} has no SKILL.md")


def _canonical_manifest(kit_dir):
    """The manifest in the exact serialization contracts/policy-gate.md pins."""
    skills = {}
    for name in sorted(RUNTIME_SKILLS):
        relative = f"skills/{name}/SKILL.md"
        with open(os.path.join(kit_dir, relative), "rb") as fh:
            digest = hashlib.sha256(fh.read()).hexdigest()
        skills[name] = {"path": relative, "sha256": digest}
    document = {"manifest_version": 1, "skills": skills}
    return json.dumps(document, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def sha256_file(path):
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def available_backends(eligibility_path=ELIGIBILITY):
    """The backends `gates/eligibility.json` records as available.

    Availability is read, never assumed. A backend whose availability gate failed has no assets in
    the kit at all, so a run can never reach half-installed material for it.
    """
    try:
        with open(eligibility_path, encoding="utf-8") as handle:
            document = json.load(handle)
    except (OSError, ValueError):
        return []
    return [name for name in BACKENDS
            if (document.get("backends") or {}).get(name, {}).get("available") is True]


def _copy_tree_files(source, target, names):
    os.makedirs(target, exist_ok=True)
    for name in names:
        shutil.copyfile(os.path.join(source, name), os.path.join(target, name))


def _stage_agents(kit_dir, backends):
    """`<KIT_DIR>/agents/`: the backend configs plus the instructions they reference."""
    agents_dir = os.path.join(kit_dir, "agents")
    os.makedirs(agents_dir, exist_ok=True)
    for backend in backends:
        source = os.path.join(AGENTS_SRC, f"{backend}.yaml")
        if os.path.isfile(source):
            shutil.copyfile(source, os.path.join(agents_dir, f"{backend}.yaml"))
    _copy_tree_files(INSTRUCTIONS_SRC, os.path.join(agents_dir, "instructions"), INSTRUCTIONS)


def _stage_claude(kit_dir, skills_source):
    """Claude's managed material, staged for the installer to place under /etc/claude-code.

    The managed `CLAUDE.md` is a byte copy of `runtime/instructions/root.md`, and the skill copies
    are byte copies of the same four files the trusted skill root holds, so both backends really do
    receive identical instructions and identical skills rather than two texts that agree in spirit.
    """
    target = os.path.join(kit_dir, "backends", "claude")
    os.makedirs(target, exist_ok=True)
    shutil.copyfile(os.path.join(CLAUDE_SRC, "managed-settings.json"),
                    os.path.join(target, "managed-settings.json"))
    shutil.copyfile(os.path.join(INSTRUCTIONS_SRC, "root.md"), os.path.join(target, "CLAUDE.md"))
    _copy_tree_files(os.path.join(CLAUDE_SRC, "agents"), os.path.join(target, "agents"), SUBAGENTS)
    for name in RUNTIME_SKILLS:
        skill_target = os.path.join(target, "skills", name)
        os.makedirs(skill_target, exist_ok=True)
        shutil.copyfile(os.path.join(skills_source, name, "SKILL.md"),
                        os.path.join(skill_target, "SKILL.md"))


#: The in-VM Docker Agent user config. It is deliberately almost empty: `permissions`, `safety`,
#: `yolo` and alias options are exactly the settings that could weaken the launcher's explicit
#: `--safety strict` or run a tool call without the gate, so the kit ships a config that has none
#: of them and the preflight fails the run if it finds any.
CODEX_USER_CONFIG = (
    "# Installed by the dca kit (tasks.md T056). Intentionally carries no `permissions`, `safety`,\n"
    "# `yolo` or alias option: those are the settings that could bypass the policy gate or weaken\n"
    "# the launcher's explicit --safety strict, so the kit/gate preflight fails the run if any of\n"
    "# them is present here.\n"
    "telemetry_enabled: false\n"
)


def _stage_codex(kit_dir):
    target = os.path.join(kit_dir, "backends", "codex")
    os.makedirs(target, exist_ok=True)
    with open(os.path.join(target, "config.yaml"), "w", encoding="utf-8") as handle:
        handle.write(CODEX_USER_CONFIG)


def _stage_artifact(kit_dir, artifact):
    """Install the pinned docker-agent binary, refusing anything but the pinned SHA-256."""
    with open(VERSIONS, encoding="utf-8") as handle:
        versions = json.load(handle)
    expected = ((versions.get("docker_agent_artifact") or {}).get("sha256") or "").lower()
    if not expected:
        raise StagingError("runtime/versions.yaml records no docker_agent_artifact.sha256")
    actual = sha256_file(artifact)
    if actual != expected:
        raise StagingError(
            f"docker-agent artifact SHA-256 {actual} does not match the pin {expected}")
    target = os.path.join(kit_dir, "bin", "docker-agent")
    shutil.copyfile(artifact, target)
    os.chmod(target, 0o755)
    return actual


def stage(prefix, python=PRODUCTION_PYTHON, skills_source=None, policy_source=POLICY_SRC,
          backends=None, artifact=None, eligibility_path=ELIGIBILITY):
    """Lay out `<prefix>/opt/dca/...`. Returns the kit directory."""
    # realpath, not abspath: the gate derives its own kit and run-state paths with
    # os.path.realpath(__file__), so a symlinked prefix would otherwise leave the wrapper pointing
    # at one path while the gate resolved to another and looked for /run/dca somewhere else.
    prefix = os.path.realpath(str(prefix))
    # The wrapper joins {PREFIX} with "opt/dca/...", so it must end in a separator exactly once.
    rendered_prefix = prefix if prefix.endswith(os.sep) else prefix + os.sep

    kit_dir = os.path.join(prefix, "opt", "dca")
    lib_dir = os.path.join(kit_dir, "lib", "dca")
    bin_dir = os.path.join(kit_dir, "bin")
    policy_dir = os.path.join(kit_dir, "policy")

    if skills_source is not None:
        _check_skills(str(skills_source))

    for directory in (lib_dir, bin_dir, policy_dir):
        os.makedirs(directory, exist_ok=True)

    # The gate package. Byte copies, so the staged modules are the reviewed ones.
    open(os.path.join(kit_dir, "lib", "__init__.py"), "w", encoding="utf-8").close()
    open(os.path.join(lib_dir, "__init__.py"), "w", encoding="utf-8").close()
    for module in GATE_MODULES:
        shutil.copyfile(os.path.join(SRC, module), os.path.join(lib_dir, module))

    for policy_file in POLICY_FILES:
        source = os.path.join(str(policy_source), policy_file)
        if os.path.exists(source):
            shutil.copyfile(source, os.path.join(policy_dir, policy_file))

    for name, template in (("dca-gate", TEMPLATE), ("dca-fingerprint", FINGERPRINT_TEMPLATE)):
        wrapper = os.path.join(bin_dir, name)
        with open(wrapper, "w", encoding="utf-8") as fh:
            fh.write(_render_wrapper(template, rendered_prefix, str(python)))
        os.chmod(wrapper, 0o755)

    if skills_source is not None:
        skills_dir = os.path.join(kit_dir, "skills")
        if os.path.isdir(skills_dir):
            shutil.rmtree(skills_dir)
        for name in RUNTIME_SKILLS:
            target = os.path.join(skills_dir, name)
            os.makedirs(target, exist_ok=True)
            shutil.copyfile(os.path.join(str(skills_source), name, "SKILL.md"),
                            os.path.join(target, "SKILL.md"))
        with open(os.path.join(kit_dir, "kit-manifest.json"), "w", encoding="utf-8") as fh:
            fh.write(_canonical_manifest(kit_dir))

    if backends is None:
        backends = available_backends(eligibility_path)
    backends = [name for name in BACKENDS if name in backends]

    if os.path.isdir(INSTRUCTIONS_SRC) and os.path.isfile(
            os.path.join(INSTRUCTIONS_SRC, "root.md")):
        _stage_agents(kit_dir, backends)
        if "claude" in backends and skills_source is not None:
            _stage_claude(kit_dir, str(skills_source))
        if "codex" in backends:
            _stage_codex(kit_dir)

    artifact_sha = _stage_artifact(kit_dir, artifact) if artifact else None

    spec = {
        "kit_version": 1,
        "backends": backends,
        "claude_managed_root": CLAUDE_MANAGED_ROOT,
        "docker_agent_artifact_sha256": artifact_sha,
        "python": str(python),
    }
    with open(os.path.join(kit_dir, "kit-spec.json"), "w", encoding="utf-8") as fh:
        fh.write(json.dumps(spec, indent=2, sort_keys=True, ensure_ascii=False) + "\n")
    return kit_dir


def main(argv):
    if len(argv) < 2:
        print("usage: python3 runtime/sandbox/kit/stage.py <prefix> [<python>] [<skills-source>]",
              file=sys.stderr)
        return 2
    prefix = argv[1]
    python = argv[2] if len(argv) > 2 else PRODUCTION_PYTHON
    skills = argv[3] if len(argv) > 3 else os.path.join(ROOT, "runtime", "skills")
    try:
        kit = stage(prefix, python=python,
                    skills_source=skills if os.path.isdir(skills) else None)
    except StagingError as exc:
        print(f"stage: {exc}", file=sys.stderr)
        return 1
    print(f"stage: wrote {kit}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))


# --- the sbx kit -----------------------------------------------------------------------------------

#: Where the kit's staged payload lands inside the sandbox image before the install step runs.
KIT_STAGING = "/home/agent/dca-kit"
#: Where the pinned docker-agent artifact is looked for on the host, unless a caller says otherwise.
DEFAULT_ARTIFACT = os.path.join(ROOT, "gates", "G6", "work", "docker-agent-linux-arm64")

SPEC_TEMPLATE = """# Production sandbox kit for docker-coding-agent-v1 (tasks.md T056).
#
# GENERATED by runtime/sandbox/kit/stage.py. Do not edit a built kit: its payload hashes are
# recorded in <KIT_DIR>/kit-manifest.json, and the in-VM preflight refuses a kit whose files do not
# match it.
#
# Mixin kit, schemaVersion 2. It declares NO `permissions.network`: every file it installs is
# staged from the host, so the sandbox needs no egress to be provisioned. That matters under the
# G0 deny-all bootstrap policy, where a kit that fetched anything would simply fail.
#
# The install step runs as root and is the only thing that writes the trusted material. It
# verifies the pinned docker-agent artifact's SHA-256 before installing it, so a substituted
# binary fails provisioning (exit 4) instead of running the task.
schemaVersion: "2"
kind: mixin
name: dca-runtime
version: "1.0.0"
displayName: docker-coding-agent-v1 runtime kit
description: Installs the pinned docker-agent, the policy gate, the trusted skill root and the\\
  backend material for the available backends.
setup:
  install:
    - description: install the dca runtime kit
      user: "0"
      command: |
        set -eu
        expected={artifact_sha256}
        actual=$(sha256sum {staging}/opt/dca/bin/docker-agent | cut -d' ' -f1)
        if [ "$actual" != "$expected" ]; then
            echo "dca kit: docker-agent artifact sha256 $actual does not match the pin $expected" >&2
            exit 1
        fi
        install -d -m 0755 /opt /opt/dca
        cp -a {staging}/opt/dca/. /opt/dca/
        chown -R 0:0 /opt/dca
        chmod 0755 /opt/dca/bin/docker-agent /opt/dca/bin/dca-gate /opt/dca/bin/dca-fingerprint
{claude_install}{codex_install}        install -d -m 0755 -o 0 -g 0 /run/dca
        install -d -m 0777 /run/dca/out /run/dca/state
        rm -rf {staging}
        /usr/bin/python3 -I /opt/dca/lib/dca/kit_preflight.py /opt/dca {codex_config_path}
"""

CLAUDE_INSTALL = """        install -d -m 0755 -o 0 -g 0 /etc/claude-code /etc/claude-code/.claude \\
            /etc/claude-code/.claude/agents /etc/claude-code/.claude/skills
        install -m 0644 -o 0 -g 0 /opt/dca/backends/claude/managed-settings.json \\
            /etc/claude-code/managed-settings.json
        install -m 0644 -o 0 -g 0 /opt/dca/backends/claude/CLAUDE.md /etc/claude-code/CLAUDE.md
        for a in dca-researcher dca-reviewer; do
            install -m 0644 -o 0 -g 0 "/opt/dca/backends/claude/agents/$a.md" \\
                "/etc/claude-code/.claude/agents/$a.md"
        done
        for s in change-receipt repository-navigation root-cause-debugging verification; do
            install -d -m 0755 -o 0 -g 0 "/etc/claude-code/.claude/skills/$s"
            install -m 0644 -o 0 -g 0 "/opt/dca/backends/claude/skills/$s/SKILL.md" \\
                "/etc/claude-code/.claude/skills/$s/SKILL.md"
        done
"""

CODEX_INSTALL = """        install -d -m 0755 /home/agent/.config /home/agent/.config/cagent
        install -m 0644 /opt/dca/backends/codex/config.yaml \\
            /home/agent/.config/cagent/config.yaml
        chown -R agent:agent /home/agent/.config || true
"""

CODEX_CONFIG_PATH = "/home/agent/.config/cagent/config.yaml"


def build_sbx_kit(destination, backends=None, artifact=None, skills_source=None,
                  eligibility_path=ELIGIBILITY):
    """Build a complete `sbx create --kit` directory. Returns its path.

    The payload is laid out by `stage()` itself, under the kit's staging path, so the bytes that
    reach the VM are the bytes `stage()` produced and hashed - there is no second copy of the
    layout logic that could drift from the manifest.
    """
    destination = os.path.abspath(str(destination))
    if os.path.isdir(destination):
        shutil.rmtree(destination)
    # Kit `files/home/` is installed at the sandbox user's home (/home/agent), not at /.
    # Using `files/home/agent/` would land the payload at /home/agent/agent/ and make the
    # installer fail its pinned-artifact check before any production asset is installed.
    payload_root = os.path.join(destination, "files", "home",
                                os.path.relpath(KIT_STAGING, "/home/agent"))
    os.makedirs(payload_root, exist_ok=True)

    artifact = artifact or DEFAULT_ARTIFACT
    if not os.path.isfile(artifact):
        raise StagingError(
            f"the pinned docker-agent artifact is not on this host: {artifact}. "
            "Set DCA_DOCKER_AGENT_ARTIFACT to its path.")

    if backends is None:
        backends = available_backends(eligibility_path)
    stage(payload_root, skills_source=skills_source or os.path.join(ROOT, "runtime", "skills"),
          backends=backends, artifact=artifact, eligibility_path=eligibility_path)

    with open(VERSIONS, encoding="utf-8") as handle:
        expected = json.load(handle)["docker_agent_artifact"]["sha256"]
    spec = SPEC_TEMPLATE.format(
        artifact_sha256=expected,
        staging=KIT_STAGING,
        claude_install=CLAUDE_INSTALL if "claude" in backends else "",
        codex_install=CODEX_INSTALL if "codex" in backends else "",
        codex_config_path=CODEX_CONFIG_PATH if "codex" in backends else "/dev/null",
    )
    with open(os.path.join(destination, "spec.yaml"), "w", encoding="utf-8") as handle:
        handle.write(spec)
    return destination
