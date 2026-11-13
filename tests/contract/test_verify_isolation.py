"""scripts/verify.sh keeps construction-time Spec Kit skills out of the runtime (tasks.md T006).

Each negative case runs verify.sh against a temporary copy of scripts/ and runtime/, so the
real tree is never modified. These are Layer A/B tests: only --static checks run, independent
of installed tool versions, daemon state or provider sign-in. Constitution V and IX.
"""

import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def run_verify(root, env=None):
    return subprocess.run(
        ["sh", str(root / "scripts" / "verify.sh"), "--static"],
        capture_output=True,
        text=True,
        check=False,
        env=env,
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

    def test_static_verification_never_invokes_live_tools(self):
        tools = self.copy / "bin"
        tools.mkdir()
        probe_log = self.copy / "live-tool-calls"
        for name in ("sbx", "docker", "claude"):
            path = tools / name
            path.write_text('#!/bin/sh\nprintf "%s\\n" "$0" >> "$DCA_VERIFY_PROBE_LOG"\n'
                            'exit 97\n', encoding="utf-8")
            path.chmod(0o755)
        environment = dict(os.environ, PATH=str(tools) + os.pathsep + os.environ["PATH"],
                           DCA_VERIFY_PROBE_LOG=str(probe_log))
        result = run_verify(ROOT, env=environment)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(probe_log.exists(), "static verification invoked a live tool")

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
