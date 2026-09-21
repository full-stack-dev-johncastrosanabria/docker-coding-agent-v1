"""Unit tests for isolated gate execution (tasks.md T031).

These exercise the EXACT installed wrapper and layout, staged by the same tool production uses with
only {PREFIX} and {PYTHON} substituted. Testing a hand-built approximation would prove nothing
about what ships.

WHAT IS ACTUALLY AT STAKE. The gate runs as a Claude PreToolUse hook inside a VM where the
repository can influence the hook runner's environment: `.claude/settings.json` can set `env`, and
the working directory is the repository itself. If any of that could steer the gate's interpreter
or its imports, a repository could substitute its own `shellparse` and decide its own policy. So
the wrapper starts from `env -i`, names absolute paths, runs `python3 -I`, and the gate rebuilds
sys.path from its own location.

And it must FAIL CLOSED while doing it: a missing module or a missing interpreter has to exit 2,
not 126 or 127, because Claude treats a non-2 non-zero exit as non-blocking - the call would
proceed.

Production's real `/opt/dca` layout with `/usr/bin/python3` is re-checked inside the VM by T062.
"""

import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

MARKER = "DCA-HOSTILE-IMPORT-MARKER"


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


stage = _load("dca_stage", ROOT / "runtime" / "sandbox" / "kit" / "stage.py")


class IsolatedExecution(unittest.TestCase):
    def setUp(self):
        self.prefix = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.prefix, ignore_errors=True)
        self.skills_source = ROOT / "runtime" / "skills"
        stage.stage(self.prefix, python=sys.executable,
                    skills_source=self._skills_source())
        self.gate = self.prefix / "opt" / "dca" / "bin" / "dca-gate"
        self._write_run_state()

    def _skills_source(self):
        """A source tree holding exactly the four runtime skills."""
        source = self.prefix / "_skills-src"
        for name in stage.RUNTIME_SKILLS:
            path = source / name / "SKILL.md"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(f"# {name}\n", encoding="utf-8")
        return source

    def _write_run_state(self):
        run_dir = self.prefix / "run" / "dca"
        (run_dir / "state").mkdir(parents=True, exist_ok=True)
        workspace = self.prefix / "workspace"
        workspace.mkdir(exist_ok=True)
        (run_dir / "out").mkdir(parents=True, exist_ok=True)
        (run_dir / "run.json").write_text(json.dumps({
            "run_id": "r-1", "trust_level": "trusted", "classification": "direct",
            "workspace": str(workspace), "scratch": str(run_dir / "out"),
            "verification_commands": [], "steps_used": 0, "step_limit": 100,
        }), encoding="utf-8")
        (run_dir / "grants.json").write_text(json.dumps({"grants": []}), encoding="utf-8")

    def payload(self, tool="Read", tool_input=None):
        return json.dumps({"hook_event_name": "PreToolUse", "tool_name": tool,
                           "tool_input": tool_input or {"file_path": "/etc/hosts"},
                           "cwd": str(self.prefix / "workspace"), "session_id": "s1"})

    def run_gate(self, cwd=None, env=None):
        environment = {"PATH": "/usr/bin:/bin", "LANG": "C.UTF-8"}
        environment.update(env or {})
        return subprocess.run([str(self.gate)], input=self.payload(), capture_output=True,
                              text=True, timeout=30, cwd=str(cwd or self.prefix),
                              env=environment)

    # --- 1. the staged wrapper works and denies -------------------------------------------------

    def test_01_shellparse_loads_and_a_deny_decision_is_produced(self):
        """A path outside the workspace is class 5; reaching that verdict needs shellparse importable."""
        result = self.run_gate()
        self.assertEqual(result.returncode, 2, result.stdout)
        self.assertIn("DCA_DENY", result.stderr)
        self.assertNotIn("internal", result.stderr, "it must be a policy denial, not a crash")

    def test_02_the_wrapper_is_executable_and_rendered_from_the_template(self):
        self.assertTrue(os.access(self.gate, os.X_OK))
        body = self.gate.read_text(encoding="utf-8")
        self.assertNotIn("{PREFIX}", body)
        self.assertNotIn("{PYTHON}", body)
        self.assertIn("env -i", body)
        self.assertIn("-I", body)
        self.assertNotIn("exec ", body, "exec would let an unmapped status escape")

    # --- 2. the current directory cannot steer imports ------------------------------------------

    def test_03_a_hostile_working_directory_cannot_substitute_shellparse(self):
        hostile = self.prefix / "hostile-repo"
        (hostile / "dca").mkdir(parents=True)
        poison = f"import sys; sys.stderr.write({MARKER!r})\n"
        (hostile / "shellparse.py").write_text(poison, encoding="utf-8")
        (hostile / "dca" / "shellparse.py").write_text(poison, encoding="utf-8")
        (hostile / "dca" / "__init__.py").write_text("", encoding="utf-8")

        result = self.run_gate(cwd=hostile)
        self.assertNotIn(MARKER, result.stderr)
        self.assertNotIn(MARKER, result.stdout)
        self.assertEqual(result.returncode, 2)

    # --- 3. hostile environment makes no difference ---------------------------------------------

    def test_04_hostile_pythonpath_and_pythonhome_are_ignored(self):
        hostile = self.prefix / "hostile-env"
        (hostile / "dca").mkdir(parents=True)
        poison = f"import sys; sys.stderr.write({MARKER!r})\n"
        (hostile / "shellparse.py").write_text(poison, encoding="utf-8")
        (hostile / "dca" / "shellparse.py").write_text(poison, encoding="utf-8")
        (hostile / "dca" / "__init__.py").write_text("", encoding="utf-8")

        result = self.run_gate(env={"PYTHONPATH": str(hostile), "PYTHONHOME": str(hostile),
                                    "PYTHONSTARTUP": str(hostile / "shellparse.py")})
        self.assertNotIn(MARKER, result.stderr + result.stdout)
        self.assertEqual(result.returncode, 2)

    def test_05_python_dash_i_directly_with_hostile_env_also_ignores_it(self):
        """The same guarantee without the wrapper, so -I is doing its part."""
        hostile = self.prefix / "hostile-direct"
        (hostile / "dca").mkdir(parents=True)
        poison = f"import sys; sys.stderr.write({MARKER!r})\n"
        (hostile / "dca" / "shellparse.py").write_text(poison, encoding="utf-8")
        (hostile / "dca" / "__init__.py").write_text("", encoding="utf-8")
        gate_module = self.prefix / "opt" / "dca" / "lib" / "dca" / "policy_gate.py"
        result = subprocess.run(
            [sys.executable, "-I", str(gate_module)], input=self.payload(),
            capture_output=True, text=True, timeout=30, cwd=str(hostile),
            env={"PATH": "/usr/bin:/bin", "LANG": "C.UTF-8",
                 "PYTHONPATH": str(hostile), "PYTHONHOME": str(hostile)})
        self.assertNotIn(MARKER, result.stderr + result.stdout)
        self.assertEqual(result.returncode, 2)

    # --- 4. a missing module fails closed -------------------------------------------------------

    def test_06_deleting_the_staged_shellparse_exits_two(self):
        (self.prefix / "opt" / "dca" / "lib" / "dca" / "shellparse.py").unlink()
        result = self.run_gate()
        self.assertEqual(result.returncode, 2)

    # --- 5. a missing interpreter fails closed, never 126/127 -----------------------------------

    def test_07_a_nonexistent_interpreter_exits_two_not_126_or_127(self):
        """Claude treats a non-2 non-zero exit as non-blocking, so 127 would let the call proceed."""
        other = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, other, ignore_errors=True)
        stage.stage(other, python=str(other / "no-such-python3"),
                    skills_source=self._skills_source())
        result = subprocess.run([str(other / "opt" / "dca" / "bin" / "dca-gate")],
                                input=self.payload(), capture_output=True, text=True, timeout=30,
                                env={"PATH": "/usr/bin:/bin", "LANG": "C.UTF-8"})
        self.assertEqual(result.returncode, 2)
        self.assertNotIn(result.returncode, (126, 127))

    def test_08_a_non_executable_interpreter_exits_two(self):
        other = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, other, ignore_errors=True)
        fake = other / "not-executable"
        fake.write_text("#!/bin/sh\n", encoding="utf-8")
        os.chmod(fake, 0o644)
        stage.stage(other, python=str(fake), skills_source=self._skills_source())
        result = subprocess.run([str(other / "opt" / "dca" / "bin" / "dca-gate")],
                                input=self.payload(), capture_output=True, text=True, timeout=30,
                                env={"PATH": "/usr/bin:/bin", "LANG": "C.UTF-8"})
        self.assertEqual(result.returncode, 2)


class StagingTool(unittest.TestCase):
    """T032: the staging tool's layout, canonical manifest and determinism."""

    def setUp(self):
        self.prefix = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.prefix, ignore_errors=True)
        self.source = self.prefix / "_src"
        for name in stage.RUNTIME_SKILLS:
            path = self.source / name / "SKILL.md"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(f"# {name}\nbody\n", encoding="utf-8")

    def stage_into(self, target):
        stage.stage(target, python=sys.executable, skills_source=self.source)
        return target / "opt" / "dca"

    def test_09_the_layout_matches_the_contract(self):
        kit = self.stage_into(self.prefix)
        for relative in ("bin/dca-gate", "lib/dca/policy_gate.py", "lib/dca/shellparse.py",
                         "lib/dca/__init__.py", "policy/actions.yaml", "kit-manifest.json"):
            with self.subTest(path=relative):
                self.assertTrue((kit / relative).exists(), relative)
        for name in stage.RUNTIME_SKILLS:
            with self.subTest(skill=name):
                self.assertTrue((kit / "skills" / name / "SKILL.md").exists())

    def test_10_the_manifest_is_in_canonical_form(self):
        kit = self.stage_into(self.prefix)
        raw = (kit / "kit-manifest.json").read_bytes()
        self.assertFalse(raw.endswith(b"\n"), "no trailing newline")
        self.assertNotIn(b", ", raw, "compact separators")
        self.assertNotIn(b": ", raw, "compact separators")
        document = json.loads(raw)
        self.assertEqual(document["manifest_version"], 1)
        self.assertEqual(sorted(document["skills"]), sorted(stage.RUNTIME_SKILLS))
        self.assertEqual(list(document["skills"]), sorted(document["skills"]), "sorted keys")
        for name, entry in document["skills"].items():
            with self.subTest(skill=name):
                self.assertEqual(sorted(entry), ["path", "sha256"])
                self.assertEqual(entry["path"], f"skills/{name}/SKILL.md")
                self.assertRegex(entry["sha256"], r"\A[0-9a-f]{64}\Z")

    def test_11_the_recorded_hash_is_of_the_staged_file(self):
        import hashlib
        kit = self.stage_into(self.prefix)
        document = json.loads((kit / "kit-manifest.json").read_text(encoding="utf-8"))
        for name, entry in document["skills"].items():
            with self.subTest(skill=name):
                body = (kit / entry["path"]).read_bytes()
                self.assertEqual(hashlib.sha256(body).hexdigest(), entry["sha256"])

    def test_12_two_staging_runs_produce_identical_bytes(self):
        first = self.stage_into(self.prefix / "a")
        second = self.stage_into(self.prefix / "b")
        for relative in ("kit-manifest.json", "policy/actions.yaml",
                         "lib/dca/policy_gate.py", "lib/dca/shellparse.py"):
            with self.subTest(path=relative):
                self.assertEqual((first / relative).read_bytes(),
                                 (second / relative).read_bytes())

    def test_13_it_refuses_any_other_skill_directory(self):
        (self.source / "extra-skill").mkdir()
        (self.source / "extra-skill" / "SKILL.md").write_text("# extra\n", encoding="utf-8")
        with self.assertRaises(stage.StagingError):
            stage.stage(self.prefix / "c", python=sys.executable, skills_source=self.source)

    def test_14_it_refuses_a_missing_runtime_skill(self):
        shutil.rmtree(self.source / "verification")
        with self.assertRaises(stage.StagingError):
            stage.stage(self.prefix / "d", python=sys.executable, skills_source=self.source)

    def test_15_only_prefix_and_python_are_substituted(self):
        """The prefix is normalized to end in a separator, because the template joins it directly
        onto `opt/dca/...` so that production's `/` yields `/opt/dca/...`. Nothing else changes."""
        kit = self.stage_into(self.prefix)
        template = (ROOT / "runtime" / "sandbox" / "kit" / "templates"
                    / "dca-gate.sh.in").read_text(encoding="utf-8")
        rendered = (kit / "bin" / "dca-gate").read_text(encoding="utf-8")
        expected = template.replace(
            "{PREFIX}", str(self.prefix.resolve()) + os.sep).replace("{PYTHON}", sys.executable)
        self.assertEqual(rendered, expected)

    def test_15b_the_production_render_is_the_contracts_exact_command(self):
        rendered = stage._render_wrapper(stage.PRODUCTION_PREFIX, stage.PRODUCTION_PYTHON)
        self.assertIn(
            "/usr/bin/env -i PATH=/usr/bin:/bin LANG=C.UTF-8 /usr/bin/python3 -I "
            "/opt/dca/lib/dca/policy_gate.py", rendered)

    def test_16_the_production_defaults_are_the_contract_paths(self):
        self.assertEqual(stage.PRODUCTION_PREFIX, "/")
        self.assertEqual(stage.PRODUCTION_PYTHON, "/usr/bin/python3")


if __name__ == "__main__":
    unittest.main()
