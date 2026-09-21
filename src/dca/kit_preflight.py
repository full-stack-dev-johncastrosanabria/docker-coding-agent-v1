"""In-VM kit and gate preflight (tasks.md T056; contracts/policy-gate.md *Kit manifest*).

Standard library only, run inside the sandbox before the agent starts. It answers one question:
**is the trusted material in this VM still the material the host staged?** If it is not, the run
does not start (launcher-cli Phase 3 step 5: exit 4, provisioning), because every control that
follows - the gate's decisions, the skill-source check, the safety mode - assumes it is.

IT FAILS CLOSED AND IT FAILS LOUDLY. Each check appends a named problem and the process exits
non-zero with all of them listed. A preflight that repaired anything, or that skipped a check it
could not perform, would let a tampered kit run under the appearance of verification, which is
strictly worse than refusing.

THE CODEX USER-CONFIG CHECK IS NOT COSMETIC. `permissions`, `safety`, `yolo` and alias options in
`~/.config/cagent/config.yaml` are exactly the settings that could run a tool call without the gate
or weaken the launcher's explicit `--safety strict`. The kit ships a config with none of them, so
finding any of them means something in the VM edited it.
"""

import hashlib
import json
import os
import re
import sys

HEX64 = re.compile(r"^[0-9a-f]{64}$")
SKILLS = ("change-receipt", "repository-navigation", "root-cause-debugging", "verification")
#: User-config keys that could bypass or weaken the launcher's pinned safety mode.
FORBIDDEN_USER_CONFIG_KEYS = ("permissions", "safety", "yolo", "alias", "aliases")


def _no_duplicate_keys(pairs):
    seen = {}
    for key, value in pairs:
        if key in seen:
            raise ValueError(f"duplicate key {key!r}")
        seen[key] = value
    return seen


def check_manifest(kit_dir, problems):
    """The canonical kit manifest, validated exactly as contracts/policy-gate.md defines it."""
    path = os.path.join(kit_dir, "kit-manifest.json")
    try:
        with open(path, "rb") as handle:
            raw = handle.read()
        document = json.loads(raw.decode("utf-8"), object_pairs_hook=_no_duplicate_keys)
    except (OSError, ValueError, UnicodeDecodeError) as exc:
        problems.append(f"kit-manifest: unreadable or invalid ({exc})")
        return None
    if not isinstance(document, dict) or set(document) != {"manifest_version", "skills"}:
        problems.append("kit-manifest: top level must have exactly manifest_version and skills")
        return None
    if document["manifest_version"] != 1:
        problems.append(f"kit-manifest: manifest_version is {document['manifest_version']!r}, not 1")
        return None
    skills = document["skills"]
    if not isinstance(skills, dict) or sorted(skills) != sorted(SKILLS):
        problems.append(f"kit-manifest: skills must be exactly {sorted(SKILLS)}, got "
                        f"{sorted(skills) if isinstance(skills, dict) else type(skills).__name__}")
        return None
    for name, entry in sorted(skills.items()):
        if not isinstance(entry, dict) or set(entry) != {"path", "sha256"}:
            problems.append(f"kit-manifest: entry {name} must have exactly path and sha256")
            continue
        if entry["path"] != f"skills/{name}/SKILL.md":
            problems.append(f"kit-manifest: entry {name} path is {entry['path']!r}")
        if not isinstance(entry["sha256"], str) or not HEX64.match(entry["sha256"]):
            problems.append(f"kit-manifest: entry {name} sha256 is not 64 lowercase hex")
    return document


def check_skill_files(kit_dir, manifest, problems):
    """Every listed skill matches its hash, and nothing unlisted exists under `skills/`."""
    if manifest is None:
        return
    listed = set()
    for name, entry in sorted(manifest["skills"].items()):
        relative = entry.get("path")
        if not isinstance(relative, str):
            continue
        listed.add(os.path.normpath(relative))
        path = os.path.join(kit_dir, relative)
        try:
            with open(path, "rb") as handle:
                digest = hashlib.sha256(handle.read()).hexdigest()
        except OSError:
            problems.append(f"kit-manifest: {relative} is missing")
            continue
        if digest != entry.get("sha256"):
            problems.append(f"kit-manifest: {relative} has sha256 {digest}, not {entry['sha256']}")

    skills_root = os.path.join(kit_dir, "skills")
    for current, _, files in os.walk(skills_root):
        for filename in files:
            absolute = os.path.join(current, filename)
            relative = os.path.normpath(os.path.relpath(absolute, kit_dir))
            if relative not in listed:
                problems.append(f"kit-manifest: {relative} is under skills/ but is not listed")


def check_gate(kit_dir, problems, python=None):
    """The gate wrapper, its interpreter and its modules are all present and usable."""
    wrapper = os.path.join(kit_dir, "bin", "dca-gate")
    if not os.path.isfile(wrapper) or not os.access(wrapper, os.X_OK):
        problems.append("gate: /opt/dca/bin/dca-gate is missing or not executable")
    for module in ("policy_gate.py", "shellparse.py", "fingerprint.py"):
        if not os.path.isfile(os.path.join(kit_dir, "lib", "dca", module)):
            problems.append(f"gate: lib/dca/{module} is missing from the trusted root")
    interpreter = python or "/usr/bin/python3"
    if not os.path.isfile(interpreter) or not os.access(interpreter, os.X_OK):
        problems.append(f"gate: the interpreter {interpreter} is missing or not executable")
    for policy_file in ("actions.yaml",):
        if not os.path.isfile(os.path.join(kit_dir, "policy", policy_file)):
            problems.append(f"gate: policy/{policy_file} is missing")


def check_codex_user_config(path, problems):
    """The in-VM Docker Agent user config must carry none of the bypass options.

    It is read as TEXT, not parsed as YAML: the runtime is stdlib-only, so there is no YAML parser
    here, and a top-level key is recognisable from the line that starts it. A textual check can
    only over-report, which is the safe direction for a preflight.
    """
    if not os.path.isfile(path):
        return
    try:
        with open(path, encoding="utf-8", errors="replace") as handle:
            lines = handle.read().splitlines()
    except OSError as exc:
        problems.append(f"codex user config: unreadable ({exc})")
        return
    for number, line in enumerate(lines, 1):
        if line.startswith("#") or not line.strip():
            continue
        key = line.split(":", 1)[0].strip()
        if key in FORBIDDEN_USER_CONFIG_KEYS:
            problems.append(
                f"codex user config: line {number} sets {key!r}, which could bypass the gate or "
                "weaken the launcher's --safety strict")


def run(kit_dir, codex_user_config=None, python=None):
    """Every problem found, as readable strings. An empty list means the kit is intact."""
    problems = []
    manifest = check_manifest(kit_dir, problems)
    check_skill_files(kit_dir, manifest, problems)
    check_gate(kit_dir, problems, python)
    if codex_user_config:
        check_codex_user_config(codex_user_config, problems)
    return problems


def main(argv=None):
    argv = list(sys.argv if argv is None else argv)
    kit_dir = argv[1] if len(argv) > 1 else os.environ.get("DOCKER_AGENT_KIT_DIR", "/opt/dca")
    codex_config = argv[2] if len(argv) > 2 else os.path.expanduser("~/.config/cagent/config.yaml")
    problems = run(kit_dir, codex_config)
    if problems:
        for problem in problems:
            print(f"preflight: FAIL {problem}", file=sys.stderr)
        print(f"preflight: FAILED ({len(problems)} problem(s))", file=sys.stderr)
        return 1
    print(f"preflight: OK kit {kit_dir} is intact")
    return 0


if __name__ == "__main__":
    sys.exit(main())
