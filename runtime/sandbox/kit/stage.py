"""Kit staging tool (tasks.md T032).

Standard library only. Lays out the gate kit under a prefix and writes the canonical kit manifest.
T055, T056, T061 and the tests all use THIS tool, so what the tests exercise is what ships: a
hand-built approximation in a test would prove nothing about production.

DETERMINISM IS A REQUIREMENT, NOT A NICETY. The manifest records a SHA-256 per skill and the gate
refuses any skill whose bytes do not match, so two staging runs from the same source must produce
identical bytes or the kit would fail its own verification. Hence canonical JSON - sorted keys,
compact separators, no trailing newline - and byte copies rather than re-serialization.

IT REFUSES ANYTHING BUT THE FOUR RUNTIME SKILLS. An extra directory under the skill source would
become an unlisted entry under the trusted skill root, which the gate treats as an invalid manifest
and which then denies every skill load. Better to fail at staging, where the message is clear.
"""

import hashlib
import json
import os
import shutil
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(HERE)))

TEMPLATE = os.path.join(HERE, "templates", "dca-gate.sh.in")
SRC = os.path.join(ROOT, "src", "dca")
POLICY_SRC = os.path.join(ROOT, "runtime", "policy")

RUNTIME_SKILLS = ("repository-navigation", "root-cause-debugging", "verification",
                  "change-receipt")

# The contract's production values. stage.py renders these unless a caller overrides them.
PRODUCTION_PREFIX = "/"
PRODUCTION_PYTHON = "/usr/bin/python3"

GATE_MODULES = ("policy_gate.py", "shellparse.py")
POLICY_FILES = ("actions.yaml",)


class StagingError(Exception):
    """The source tree is not something that may be staged."""


def _render_wrapper(prefix, python):
    with open(TEMPLATE, encoding="utf-8") as fh:
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


def stage(prefix, python=PRODUCTION_PYTHON, skills_source=None, policy_source=POLICY_SRC):
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

    wrapper = os.path.join(bin_dir, "dca-gate")
    with open(wrapper, "w", encoding="utf-8") as fh:
        fh.write(_render_wrapper(rendered_prefix, str(python)))
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
