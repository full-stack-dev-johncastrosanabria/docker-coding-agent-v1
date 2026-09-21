"""Unit tests for the policy gate (tasks.md T029, src/dca/policy_gate.py).

The gate is invoked as a process with the hook payload on stdin, exactly as the backends invoke it,
because its contract is expressed in exit codes and stdout bytes rather than in a Python API. Each
test therefore stages a kit layout under a temporary prefix and runs the real module against it.

THE ONE RULE EVERYTHING ELSE SERVES: no code path exits 0 without a decision. For Claude that means
exit 0 and silence; for Codex it means exit 0 AND the snake_case decision on stdout, because an
exit 0 with no decision falls through to confirmation and is rejected by `--exec --json`. Anything
unexpected - an unknown agent, a malformed payload, an internal exception - is exit 2.

Class 26 is the subtle one. It is normatively DENY, so an ALLOWED delegation or skill load is its
POSITIVE COMPLEMENT and is logged as `class: null` with `rule: class26-positive-complement`, never
as `class: 26` with `decision: allow`. The skill NAME is never sufficient: the trusted source must
verify against the canonical kit manifest, and an invalid manifest denies every skill load.
"""

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
GATE_SOURCE = ROOT / "src" / "dca" / "policy_gate.py"
SHELLPARSE_SOURCE = ROOT / "src" / "dca" / "shellparse.py"
POLICY_SOURCE = ROOT / "runtime" / "policy" / "actions.yaml"

SKILLS = ("repository-navigation", "root-cause-debugging", "verification", "change-receipt")
CODEX_ALLOW = {"hook_specific_output": {"hook_event_name": "pre_tool_use",
                                        "permission_decision": "allow"}}


class GateHarness(unittest.TestCase):
    """Stages `<prefix>/opt/dca/...` and `<prefix>/run/dca/...` and runs the real gate there."""

    def setUp(self):
        self.prefix = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.prefix, ignore_errors=True)

        self.lib = self.prefix / "opt" / "dca" / "lib" / "dca"
        self.lib.mkdir(parents=True)
        (self.lib.parent / "__init__.py").write_text("", encoding="utf-8")
        (self.lib / "__init__.py").write_text("", encoding="utf-8")
        shutil.copy(GATE_SOURCE, self.lib / "policy_gate.py")
        shutil.copy(SHELLPARSE_SOURCE, self.lib / "shellparse.py")

        self.kit = self.prefix / "opt" / "dca"
        (self.kit / "policy").mkdir(parents=True, exist_ok=True)
        shutil.copy(POLICY_SOURCE, self.kit / "policy" / "actions.yaml")

        self.workspace = self.prefix / "workspace"
        (self.workspace / "src").mkdir(parents=True)
        (self.workspace / "src" / "app.py").write_text("x = 1\n", encoding="utf-8")
        (self.workspace / ".env").write_text("SECRET=shhh\n", encoding="utf-8")

        self.state = self.prefix / "run" / "dca" / "state"
        self.state.mkdir(parents=True)
        (self.prefix / "run" / "dca" / "out").mkdir(parents=True)
        self.write_run()
        self.write_grants([])
        self.stage_skills()

    # --- staging ------------------------------------------------------------------------------

    def stage_skills(self, bodies=None, manifest=None, extra=None):
        """The trusted skill root plus the canonical manifest, in the exact contract form."""
        skills_dir = self.kit / "skills"
        if skills_dir.exists():
            shutil.rmtree(skills_dir)
        entries = {}
        for name in SKILLS:
            body = (bodies or {}).get(name, f"# {name}\nTrusted runtime skill.\n")
            path = skills_dir / name / "SKILL.md"
            path.parent.mkdir(parents=True)
            path.write_bytes(body.encode("utf-8"))
            entries[name] = {"path": f"skills/{name}/SKILL.md",
                             "sha256": hashlib.sha256(body.encode("utf-8")).hexdigest()}
        if extra:
            (skills_dir / extra).mkdir(parents=True, exist_ok=True)
            (skills_dir / extra / "SKILL.md").write_text("# extra\n", encoding="utf-8")
        document = manifest if manifest is not None else {"manifest_version": 1,
                                                          "skills": entries}
        raw = document if isinstance(document, str) else json.dumps(
            document, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        (self.kit / "kit-manifest.json").write_text(raw, encoding="utf-8")

    def write_run(self, **overrides):
        run = {
            "run_id": "r-1", "trust_level": "trusted", "classification": "direct",
            "workspace": str(self.workspace), "scratch": str(self.prefix / "run" / "dca" / "out"),
            "verification_commands": ["make test"],
            "steps_used": 0, "step_limit": 100, "retries_used": 0, "retry_limit": 5,
        }
        run.update(overrides)
        (self.prefix / "run" / "dca" / "run.json").write_text(json.dumps(run), encoding="utf-8")

    def write_grants(self, grants):
        (self.prefix / "run" / "dca" / "grants.json").write_text(
            json.dumps({"grants": grants}), encoding="utf-8")

    # --- invocation ---------------------------------------------------------------------------

    def run_gate(self, payload):
        raw = payload if isinstance(payload, str) else json.dumps(payload)
        return subprocess.run(
            [sys.executable, "-I", str(self.lib / "policy_gate.py")],
            input=raw, capture_output=True, text=True, timeout=30,
            env={"PATH": "/usr/bin:/bin", "LANG": "C.UTF-8"})

    def claude(self, tool, tool_input=None, agent_type=None):
        payload = {"hook_event_name": "PreToolUse", "tool_name": tool,
                   "tool_input": tool_input or {}, "cwd": str(self.workspace),
                   "session_id": "s1"}
        if agent_type:
            payload["agent_type"] = agent_type
        return payload

    def codex(self, tool, tool_input=None, agent_name="root"):
        payload = {"hook_event_name": "pre_tool_use", "tool_name": tool,
                   "tool_input": tool_input or {}, "cwd": str(self.workspace),
                   "session_id": "s1"}
        if agent_name is not None:
            payload["agent_name"] = agent_name
        return payload

    def log(self):
        path = self.state / "gate.log.jsonl"
        if not path.exists():
            return []
        return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]

    def approvals(self):
        path = self.state / "approvals.jsonl"
        if not path.exists():
            return []
        return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]

    # --- assertions ---------------------------------------------------------------------------

    def assertAllowClaude(self, result):
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "", "Claude ALLOW is exit 0 with no output")

    def assertAllowCodex(self, result):
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout), CODEX_ALLOW)

    def assertDeny(self, result, action_class=None):
        self.assertEqual(result.returncode, 2, result.stdout)
        self.assertIn("DCA_DENY", result.stderr)
        if action_class is not None:
            self.assertIn(str(action_class), result.stderr)


class Normalization(GateHarness):
    def test_01_claude_agent_type_maps_to_the_role(self):
        for agent_type, expected in (("dca-researcher", "researcher"),
                                     ("dca-reviewer", "reviewer"), (None, "root")):
            with self.subTest(agent_type=agent_type):
                self.run_gate(self.claude("Read", {"file_path": str(self.workspace / "src/app.py")},
                                          agent_type=agent_type))
                self.assertEqual(self.log()[-1]["agent"], expected)

    def test_02_codex_agent_name_maps_to_the_role(self):
        for name in ("root", "researcher", "reviewer"):
            with self.subTest(agent_name=name):
                self.run_gate(self.codex("read_file",
                                         {"path": str(self.workspace / "src/app.py")},
                                         agent_name=name))
                self.assertEqual(self.log()[-1]["agent"], name)

    def test_03_a_missing_or_unknown_codex_agent_name_denies(self):
        for name in (None, "", "orchestrator", "root2", 7):
            with self.subTest(agent_name=name):
                payload = self.codex("read_file", {"path": "x"}, agent_name=name)
                payload.pop("agent_name", None) if name is None else None
                self.assertEqual(self.run_gate(payload).returncode, 2)

    def test_04_a_malformed_payload_denies(self):
        for payload in ("", "not json", "[]", "null", '{"tool_name":1}'):
            with self.subTest(payload=payload):
                self.assertEqual(self.run_gate(payload).returncode, 2)


class DecisionOutput(GateHarness):
    def test_05_claude_allow_is_exit_zero_and_silent(self):
        self.assertAllowClaude(
            self.run_gate(self.claude("Read", {"file_path": str(self.workspace / "src/app.py")})))

    def test_06_codex_allow_carries_the_exact_snake_case_decision(self):
        result = self.run_gate(self.codex("read_file",
                                          {"path": str(self.workspace / "src/app.py")}))
        self.assertAllowCodex(result)
        self.assertIn("permission_decision", result.stdout)
        self.assertNotIn("permissionDecision", result.stdout)

    def test_07_no_path_exits_zero_without_a_codex_decision(self):
        """An exit 0 with no decision falls through to confirmation and is rejected."""
        payloads = [
            self.codex("read_file", {"path": str(self.workspace / "src/app.py")}),
            self.codex("read_file", {"path": str(self.workspace / ".env")}),
            self.codex("write_file", {"path": str(self.workspace / "src/app.py")}),
            self.codex("shell", {"command": "make test"}),
            self.codex("shell", {"command": "curl http://example.invalid"}),
            self.codex("read_skill", {"name": "verification"}),
            self.codex("transfer_task", {"agent": "researcher"}),
            self.codex("web_search", {"query": "x"}),
        ]
        for payload in payloads:
            with self.subTest(tool=payload["tool_name"]):
                result = self.run_gate(payload)
                if result.returncode == 0:
                    self.assertEqual(json.loads(result.stdout), CODEX_ALLOW)
                else:
                    self.assertEqual(result.returncode, 2)

    def test_08_every_deny_or_ungranted_ask_is_exit_two(self):
        for payload in (self.claude("Read", {"file_path": str(self.workspace / ".env")}),
                        self.claude("Bash", {"command": "curl http://example.invalid/api"})):
            with self.subTest(tool=payload["tool_name"]):
                self.assertEqual(self.run_gate(payload).returncode, 2)


class Paths(GateHarness):
    def test_09_a_sensitive_path_is_class_2(self):
        self.assertDeny(
            self.run_gate(self.claude("Read", {"file_path": str(self.workspace / ".env")})), 2)

    def test_10_a_symlink_escape_is_class_5(self):
        link = self.workspace / "escape"
        os.symlink("/etc", link)
        self.assertDeny(
            self.run_gate(self.claude("Read", {"file_path": str(link / "hosts")})), 5)

    def test_11_a_path_outside_the_workspace_is_class_5(self):
        self.assertDeny(
            self.run_gate(self.claude("Read", {"file_path": "/etc/hosts"})), 5)

    def test_12_a_workspace_file_is_allowed(self):
        self.assertAllowClaude(
            self.run_gate(self.claude("Read", {"file_path": str(self.workspace / "src/app.py")})))

    def test_13_the_scratch_dir_is_allowed(self):
        scratch = self.prefix / "run" / "dca" / "out" / "context.json"
        self.assertAllowClaude(self.run_gate(self.claude("Write", {"file_path": str(scratch)})))


class ReadOnlyRoles(GateHarness):
    def test_14_a_mutating_tool_by_researcher_or_reviewer_is_class_27(self):
        for agent in ("dca-researcher", "dca-reviewer"):
            for tool in ("Write", "Edit", "Bash"):
                with self.subTest(agent=agent, tool=tool):
                    payload = self.claude(tool, {"file_path": str(self.workspace / "src/app.py"),
                                                 "command": "make test"}, agent_type=agent)
                    self.assertDeny(self.run_gate(payload), 27)

    def test_15_the_codex_reviewers_fixed_git_tools_are_read_only_inspection(self):
        for tool in ("git_diff", "git_status", "git_log"):
            with self.subTest(tool=tool):
                self.assertAllowCodex(
                    self.run_gate(self.codex(tool, {}, agent_name="reviewer")))

    def test_16_a_read_by_researcher_is_allowed(self):
        self.assertAllowClaude(self.run_gate(self.claude(
            "Read", {"file_path": str(self.workspace / "src/app.py")},
            agent_type="dca-researcher")))


class StepLimit(GateHarness):
    def test_17_a_tool_call_after_the_step_limit_is_class_29(self):
        self.write_run(steps_used=100, step_limit=100)
        result = self.run_gate(self.claude("Read",
                                           {"file_path": str(self.workspace / "src/app.py")}))
        self.assertEqual(result.returncode, 2)
        self.assertIn("DCA_LIMIT", result.stderr)
        self.assertEqual(self.log()[-1]["class"], 29)

    def test_18_the_limit_message_tells_the_agent_to_stop_and_report(self):
        self.write_run(steps_used=100, step_limit=100)
        stderr = self.run_gate(self.claude("Read", {"file_path": "x"})).stderr
        self.assertIn("stop now", stderr.lower())
        self.assertIn("blocked", stderr.lower())


class Class26PositiveComplement(GateHarness):
    def test_19_root_delegating_to_researcher_or_reviewer_is_allowed(self):
        for payload in (self.codex("transfer_task", {"agent": "researcher"}),
                        self.codex("transfer_task", {"agent": "reviewer"})):
            with self.subTest(target=payload["tool_input"]["agent"]):
                self.assertAllowCodex(self.run_gate(payload))
        for subagent in ("dca-researcher", "dca-reviewer"):
            with self.subTest(subagent=subagent):
                self.assertAllowClaude(
                    self.run_gate(self.claude("Task", {"subagent_type": subagent})))

    def test_20_root_loading_each_runtime_skill_is_allowed(self):
        for name in SKILLS:
            with self.subTest(skill=name):
                self.assertAllowCodex(self.run_gate(self.codex("read_skill", {"name": name})))
                self.assertAllowClaude(self.run_gate(self.claude("Skill", {"skill": name})))

    def test_21_a_complement_allow_is_logged_as_class_null_with_its_rule(self):
        self.run_gate(self.codex("read_skill", {"name": "verification"}))
        entry = self.log()[-1]
        self.assertIsNone(entry["class"])
        self.assertEqual(entry["rule"], "class26-positive-complement")
        self.assertEqual(entry["decision"], "allow")

    def test_22_a_complement_allow_is_never_logged_as_class_26_allow(self):
        for payload in (self.codex("read_skill", {"name": "verification"}),
                        self.codex("transfer_task", {"agent": "researcher"})):
            self.run_gate(payload)
        for entry in self.log():
            self.assertFalse(entry["class"] == 26 and entry["decision"] == "allow")

    def test_23_every_logged_class_is_1_to_31_or_null(self):
        self.run_gate(self.codex("read_skill", {"name": "verification"}))
        self.run_gate(self.codex("read_skill", {"name": "nope"}))
        self.run_gate(self.claude("Read", {"file_path": str(self.workspace / ".env")}))
        for entry in self.log():
            with self.subTest(entry=entry):
                self.assertTrue(entry["class"] is None or 1 <= entry["class"] <= 31)


class Class26Violations(GateHarness):
    def test_24_a_non_allowlisted_subagent_or_skill_is_class_26(self):
        for payload in (self.codex("transfer_task", {"agent": "deployer"}),
                        self.codex("read_skill", {"name": "not-a-runtime-skill"}),
                        self.claude("Task", {"subagent_type": "dca-deployer"}),
                        self.claude("Skill", {"skill": "not-a-runtime-skill"})):
            with self.subTest(payload=payload["tool_input"]):
                self.assertDeny(self.run_gate(payload), 26)

    def test_25_run_skill_is_never_allowed(self):
        for name in SKILLS:
            with self.subTest(skill=name):
                self.assertDeny(self.run_gate(self.codex("run_skill", {"name": name})), 26)

    def test_26_the_name_alone_is_not_enough_when_the_copy_is_missing(self):
        shutil.rmtree(self.kit / "skills" / "verification")
        self.assertDeny(self.run_gate(self.codex("read_skill", {"name": "verification"})), 26)

    def test_27_a_hash_mismatch_denies(self):
        (self.kit / "skills" / "verification" / "SKILL.md").write_text("tampered\n",
                                                                       encoding="utf-8")
        self.assertDeny(self.run_gate(self.codex("read_skill", {"name": "verification"})), 26)

    def test_28_delegation_or_skill_loads_by_researcher_or_reviewer_are_class_26(self):
        for agent in ("researcher", "reviewer"):
            for payload in (self.codex("transfer_task", {"agent": "reviewer"}, agent_name=agent),
                            self.codex("read_skill", {"name": "verification"}, agent_name=agent)):
                with self.subTest(agent=agent, tool=payload["tool_name"]):
                    self.assertDeny(self.run_gate(payload), 26)

    def test_29_a_class_26_violation_is_logged_as_class_26_deny(self):
        self.run_gate(self.codex("read_skill", {"name": "nope"}))
        entry = self.log()[-1]
        self.assertEqual(entry["class"], 26)
        self.assertEqual(entry["decision"], "deny")


class KitManifestFailures(GateHarness):
    """An invalid manifest is an unverifiable source, so EVERY skill load denies."""

    def assertAllSkillLoadsDeny(self):
        for name in SKILLS:
            with self.subTest(skill=name):
                self.assertDeny(self.run_gate(self.codex("read_skill", {"name": name})), 26)

    def test_30_a_missing_manifest_denies_every_skill_load(self):
        (self.kit / "kit-manifest.json").unlink()
        self.assertAllSkillLoadsDeny()

    def test_31_malformed_json_denies(self):
        self.stage_skills(manifest="{not json")
        self.assertAllSkillLoadsDeny()

    def test_32_a_duplicate_key_denies(self):
        self.stage_skills(manifest='{"manifest_version":1,"manifest_version":1,"skills":{}}')
        self.assertAllSkillLoadsDeny()

    def test_33_a_wrong_manifest_version_denies(self):
        entries = json.loads((self.kit / "kit-manifest.json").read_text())["skills"]
        self.stage_skills(manifest={"manifest_version": 2, "skills": entries})
        self.assertAllSkillLoadsDeny()

    def test_34_a_missing_or_extra_skill_entry_denies(self):
        entries = json.loads((self.kit / "kit-manifest.json").read_text())["skills"]
        short = dict(entries)
        short.pop("verification")
        self.stage_skills(manifest={"manifest_version": 1, "skills": short})
        self.assertAllSkillLoadsDeny()
        self.stage_skills()
        extra = dict(entries, bonus={"path": "skills/bonus/SKILL.md", "sha256": "a" * 64})
        self.stage_skills(manifest={"manifest_version": 1, "skills": extra})
        self.assertAllSkillLoadsDeny()

    def test_35_an_extra_property_denies(self):
        entries = json.loads((self.kit / "kit-manifest.json").read_text())["skills"]
        entries["verification"]["extra"] = 1
        self.stage_skills(manifest={"manifest_version": 1, "skills": entries})
        self.assertAllSkillLoadsDeny()

    def test_36_a_wrong_path_denies(self):
        entries = json.loads((self.kit / "kit-manifest.json").read_text())["skills"]
        entries["verification"]["path"] = "skills/../skills/verification/SKILL.md"
        self.stage_skills(manifest={"manifest_version": 1, "skills": entries})
        self.assertAllSkillLoadsDeny()

    def test_37_a_non_64_lowercase_hex_sha256_denies(self):
        for bad in ("A" * 64, "abc", "g" * 64, "sha256:" + "a" * 64):
            with self.subTest(sha256=bad[:12]):
                entries = json.loads((self.kit / "kit-manifest.json").read_text())["skills"]
                entries["verification"]["sha256"] = bad
                self.stage_skills(manifest={"manifest_version": 1, "skills": entries})
                self.assertDeny(
                    self.run_gate(self.codex("read_skill", {"name": "verification"})), 26)

    def test_38_an_unlisted_extra_skill_directory_denies(self):
        self.stage_skills(extra="bonus")
        self.assertAllSkillLoadsDeny()


class NetworkIntent(GateHarness):
    def test_39_an_external_api_call_is_class_21_ask(self):
        result = self.run_gate(self.claude("Bash", {"command": "curl https://api.example.invalid/v1"}))
        self.assertEqual(result.returncode, 2)
        self.assertIn("DCA_APPROVAL_REQUIRED", result.stderr)
        self.assertEqual(self.log()[-1]["class"], 21)

    def test_40_a_web_search_tool_is_class_30_deny(self):
        for payload in (self.claude("WebSearch", {"query": "x"}),
                        self.codex("web_search", {"query": "x"})):
            with self.subTest(tool=payload["tool_name"]):
                self.assertDeny(self.run_gate(payload), 30)

    def test_41_fetching_a_non_listed_page_is_class_30_deny(self):
        self.assertDeny(
            self.run_gate(self.claude("WebFetch", {"url": "https://example.invalid/page"})), 30)

    def test_42_a_policy_listed_documentation_host_is_class_31(self):
        result = self.run_gate(self.claude("WebFetch", {"url": "https://docs.claude.com/en/x"}))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.log()[-1]["class"], 31)

    def test_43_documentation_is_ask_for_an_untrusted_run(self):
        self.write_run(trust_level="untrusted")
        result = self.run_gate(self.claude("WebFetch", {"url": "https://docs.claude.com/en/x"}))
        self.assertEqual(result.returncode, 2)
        self.assertIn("DCA_APPROVAL_REQUIRED", result.stderr)


class Approvals(GateHarness):
    def test_44_an_ask_without_a_grant_records_a_request(self):
        result = self.run_gate(self.claude("Bash",
                                           {"command": "curl https://api.example.invalid/v1"}))
        self.assertEqual(result.returncode, 2)
        self.assertIn("DCA_APPROVAL_REQUIRED", result.stderr)
        requests = self.approvals()
        self.assertEqual(len(requests), 1)
        self.assertEqual(requests[0]["status"], "requested")
        self.assertEqual(requests[0]["action_class"], 21)
        self.assertTrue(requests[0]["id"].startswith("apr-r-1-"))

    def test_45_the_request_id_appears_in_the_stderr_line(self):
        result = self.run_gate(self.claude("Bash", {"command": "curl https://api.x.invalid/v1"}))
        self.assertIn(self.approvals()[0]["id"], result.stderr)

    def test_46_a_matching_grant_turns_the_ask_into_an_allow(self):
        probe = self.run_gate(self.claude("Bash", {"command": "curl https://api.x.invalid/v1"}))
        self.assertEqual(probe.returncode, 2)
        request = self.approvals()[0]
        self.write_grants([{"request_id": request["id"], "action_class": 21,
                            "normalized_target": request["normalized_target"],
                            "granted_by": "developer-cli"}])
        result = self.run_gate(self.claude("Bash", {"command": "curl https://api.x.invalid/v1"}))
        self.assertAllowClaude(result)

    def test_47_a_grant_never_turns_a_deny_class_into_an_allow(self):
        self.write_grants([{"request_id": "apr-r-1-1", "action_class": 2,
                            "normalized_target": str(self.workspace / ".env"),
                            "granted_by": "developer-cli"}])
        self.assertDeny(
            self.run_gate(self.claude("Read", {"file_path": str(self.workspace / ".env")})), 2)

    def test_48_a_grant_for_a_different_target_does_not_apply(self):
        self.run_gate(self.claude("Bash", {"command": "curl https://api.x.invalid/v1"}))
        request = self.approvals()[0]
        self.write_grants([{"request_id": request["id"], "action_class": 21,
                            "normalized_target": "https://other.invalid/",
                            "granted_by": "developer-cli"}])
        result = self.run_gate(self.claude("Bash", {"command": "curl https://api.x.invalid/v1"}))
        self.assertEqual(result.returncode, 2)


class FailClosed(GateHarness):
    def test_49_an_unparseable_command_is_class_28_ask(self):
        result = self.run_gate(self.claude("Bash", {"command": "eval $CMD"}))
        self.assertEqual(result.returncode, 2)
        self.assertEqual(self.log()[-1]["class"], 28)

    def test_50_a_missing_shellparse_module_exits_two(self):
        (self.lib / "shellparse.py").unlink()
        result = self.run_gate(self.claude("Bash", {"command": "make test"}))
        self.assertEqual(result.returncode, 2)

    def test_51_an_unreadable_policy_exits_two(self):
        (self.kit / "policy" / "actions.yaml").write_text("{broken", encoding="utf-8")
        self.assertEqual(self.run_gate(self.claude("Read", {"file_path": "x"})).returncode, 2)

    def test_52_a_missing_run_state_exits_two(self):
        (self.prefix / "run" / "dca" / "run.json").unlink()
        self.assertEqual(self.run_gate(self.claude("Read", {"file_path": "x"})).returncode, 2)

    def test_53_an_internal_exception_exits_two_with_dca_deny(self):
        (self.prefix / "run" / "dca" / "run.json").write_text("{not json", encoding="utf-8")
        result = self.run_gate(self.claude("Read", {"file_path": "x"}))
        self.assertEqual(result.returncode, 2)
        self.assertIn("DCA_DENY", result.stderr)


class LogHygiene(GateHarness):
    def test_54_the_log_never_records_file_contents_or_secrets(self):
        self.run_gate(self.claude("Read", {"file_path": str(self.workspace / ".env")}))
        self.run_gate(self.claude("Write", {"file_path": str(self.workspace / "src/app.py"),
                                            "content": "SECRET=shhh\nPASSWORD=hunter2\n"}))
        self.run_gate(self.claude("Bash", {"command": "echo sk-ant-oat01-abcdefghijklmnop"}))
        blob = json.dumps(self.log())
        for leaked in ("shhh", "hunter2", "sk-ant-oat01"):
            with self.subTest(value=leaked):
                self.assertNotIn(leaked, blob)

    def test_55_every_log_entry_has_the_contract_fields(self):
        self.run_gate(self.claude("Read", {"file_path": str(self.workspace / "src/app.py")}))
        entry = self.log()[-1]
        for field in ("ts", "agent", "tool", "class", "decision", "counters"):
            with self.subTest(field=field):
                self.assertIn(field, entry)


if __name__ == "__main__":
    unittest.main()
