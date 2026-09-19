"""scripts/verify.sh keeps construction-time Spec Kit skills out of the runtime (tasks.md T006).

Each negative case runs verify.sh against a temporary copy of scripts/ and runtime/, so the
real tree is never modified. Constitution V and IX.
"""

import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def run_verify(root):
    return subprocess.run(
        ["sh", str(root / "scripts" / "verify.sh")],
        capture_output=True,
        text=True,
        check=False,
    )


class VerifyIsolation(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.copy = Path(self._tmp.name)
        shutil.copytree(ROOT / "scripts", self.copy / "scripts")
        shutil.copytree(ROOT / "runtime", self.copy / "runtime")

    def tearDown(self):
        self._tmp.cleanup()

    def test_repository_passes(self):
        result = run_verify(ROOT)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_clean_copy_passes(self):
        result = run_verify(self.copy)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("verify: OK", result.stdout)

    def test_planted_speckit_skill_fails_naming_the_path(self):
        planted = self.copy / "runtime" / "skills" / "speckit-plan" / "SKILL.md"
        planted.parent.mkdir(parents=True)
        planted.write_text("---\nname: speckit-plan\n---\n", encoding="utf-8")
        result = run_verify(self.copy)
        self.assertEqual(result.returncode, 1)
        self.assertIn("runtime/skills/speckit-plan", result.stderr)

    def test_speckit_skill_in_kit_fails(self):
        planted = self.copy / "runtime" / "sandbox" / "kit" / "skills" / "speckit-tasks" / "SKILL.md"
        planted.parent.mkdir(parents=True)
        planted.write_text("x\n", encoding="utf-8")
        result = run_verify(self.copy)
        self.assertEqual(result.returncode, 1)
        self.assertIn("runtime/sandbox/kit/skills/speckit-tasks", result.stderr)

    def test_claude_skills_path_under_runtime_fails(self):
        planted = self.copy / "runtime" / "sandbox" / "kit" / ".claude" / "skills" / "x" / "SKILL.md"
        planted.parent.mkdir(parents=True)
        planted.write_text("x\n", encoding="utf-8")
        result = run_verify(self.copy)
        self.assertEqual(result.returncode, 1)
        self.assertIn("runtime/sandbox/kit/.claude/skills", result.stderr)

    def test_agent_config_referencing_bootstrap_fails(self):
        config = self.copy / "runtime" / "agents" / "codex.yaml"
        config.write_text("# see docker-agent.bootstrap.yaml\n", encoding="utf-8")
        result = run_verify(self.copy)
        self.assertEqual(result.returncode, 1)
        self.assertIn("runtime/agents/codex.yaml", result.stderr)

    def test_missing_versions_file_fails(self):
        (self.copy / "runtime" / "versions.yaml").unlink()
        result = run_verify(self.copy)
        self.assertEqual(result.returncode, 1)
        self.assertIn("runtime/versions.yaml", result.stderr)


if __name__ == "__main__":
    unittest.main()
