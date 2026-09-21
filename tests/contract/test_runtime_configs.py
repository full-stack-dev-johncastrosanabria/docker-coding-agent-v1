"""Static contract tests for the Docker Agent backend configs (tasks.md T059).

CI Layer B: credential-free, no sandbox, no network. The vendored root schema
(`agent-schema-v1.136.0.json`) is a **sanity check only** - it describes config version 16 and
merely accepts `"15"`. The compatibility authority for v15 is the pinned binary's strict parser,
exercised by T055/T061 (`docker agent debug config`) and inside the VM by T062. So the assertions
that matter here are the specific ones below, not the schema pass.

WHY EACH ASSERTION IS SEPARATE AND LITERAL. Every one of them corresponds to a control that has no
other enforcement point in the system: `safety: strict` and the absence of `permissions.allow` are
what make the gate see every call; the reviewer's exact capability set is what makes it read-only;
the top-level `budget` is what makes native ceilings run-wide rather than per agent. A config edit
that quietly drops one of these produces a system that still starts and still looks right.

AN UNAVAILABLE BACKEND IS SKIPPED WITH ITS REASON, never silently passed. A backend recorded
NOT-APPLICABLE in `gates/eligibility.json` has no assets to check, and reporting a pass for assets
that do not exist would be the most misleading result available.
"""

import importlib.util
import json
import sys
import unittest
from pathlib import Path

import jsonschema
import yaml

ROOT = Path(__file__).resolve().parents[2]
AGENTS = ROOT / "runtime" / "agents"
SCHEMA = Path(__file__).resolve().parent / "agent-schema-v1.136.0.json"
ELIGIBILITY = ROOT / "gates" / "eligibility.json"
LIMITS = json.loads((ROOT / "runtime" / "policy" / "limits.yaml").read_text(encoding="utf-8"))


def _load(name, path):
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


policy_rules = _load("dca_policy_rules", ROOT / "src" / "dca" / "policy_rules.py")


def eligibility():
    return json.loads(ELIGIBILITY.read_text(encoding="utf-8"))


def availability(backend):
    """(available, reason). The reason is what a skip has to say out loud."""
    entry = (eligibility().get("backends") or {}).get(backend) or {}
    if entry.get("available") is True:
        return True, ""
    return False, (entry.get("unavailable_reason")
                   or f"backend unavailable per gates/eligibility.json ({backend})")


def require(backend):
    available, reason = availability(backend)
    if not available:
        raise unittest.SkipTest(f"NOT-APPLICABLE: {reason}")
    path = AGENTS / f"{backend}.yaml"
    if not path.is_file():
        raise unittest.SkipTest(f"NOT-APPLICABLE: {backend}.yaml was not built ({reason})")
    return yaml.safe_load(path.read_text(encoding="utf-8"))


class TestSchemaSanity(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
        jsonschema.Draft202012Validator.check_schema(cls.schema)

    def test_01_each_available_backend_config_validates_against_the_vendored_schema(self):
        for backend in ("claude", "codex"):
            with self.subTest(backend=backend):
                config = require(backend)
                jsonschema.Draft202012Validator(self.schema).validate(config)

    def test_02_both_configs_declare_config_version_15_as_a_string(self):
        for backend in ("claude", "codex"):
            with self.subTest(backend=backend):
                self.assertEqual(require(backend)["version"], "15")


class TestClaudeConfig(unittest.TestCase):
    def setUp(self):
        self.config = require("claude")
        self.agents = self.config["agents"]

    def test_10_it_has_exactly_one_agent_and_that_agent_is_a_harness(self):
        self.assertEqual(sorted(self.agents), ["root"])
        self.assertEqual(self.agents["root"]["harness"]["type"], "claude-code")
        self.assertEqual(self.agents["root"]["harness"]["effort"], "high")

    def test_11_it_pins_no_harness_model(self):
        self.assertNotIn("model", self.agents["root"]["harness"])
        self.assertNotIn("model", self.agents["root"])

    def test_12_it_declares_no_toolsets_sub_agents_or_instruction_file(self):
        for key in ("toolsets", "sub_agents", "instruction_file", "instruction", "skills"):
            with self.subTest(key=key):
                self.assertNotIn(key, self.agents["root"])

    def test_13_its_only_hooks_are_turn_and_stop_run_record_hooks(self):
        hooks = self.agents["root"].get("hooks") or {}
        self.assertEqual(sorted(hooks), ["stop", "turn_end"])
        self.assertNotIn("pre_tool_use", hooks)

    def test_14_it_has_no_code_mode_tools_mcp_or_permissions(self):
        for key in ("code_mode_tools", "mcps", "permissions", "flavors", "rag"):
            with self.subTest(key=key):
                self.assertNotIn(key, self.config)
                self.assertNotIn(key, self.agents["root"])


class TestCodexConfig(unittest.TestCase):
    def setUp(self):
        self.config = require("codex")
        self.agents = self.config["agents"]
        self.raw = (AGENTS / "codex.yaml").read_text(encoding="utf-8")

    # --- safety mode -----------------------------------------------------------------------

    def test_20_every_agent_declares_safety_strict(self):
        self.assertEqual(sorted(self.agents), ["researcher", "reviewer", "root"])
        for name, agent in sorted(self.agents.items()):
            with self.subTest(agent=name):
                self.assertEqual(agent.get("safety"), "strict")

    def test_21_the_value_restricted_appears_nowhere(self):
        # Comment lines are excluded on purpose: the header explains that `restricted` is never
        # used, and a test that forbade the word would forbid documenting the decision.
        body = "\n".join(line for line in self.raw.splitlines()
                         if not line.lstrip().startswith("#"))
        self.assertNotIn("restricted", body)

    def test_22_no_agent_declares_a_harness(self):
        for name, agent in sorted(self.agents.items()):
            with self.subTest(agent=name):
                self.assertNotIn("harness", agent)

    # --- the policy pipeline ---------------------------------------------------------------

    def test_23_permissions_has_a_deny_list_and_nothing_else(self):
        permissions = self.config["permissions"]
        self.assertEqual(sorted(permissions), ["deny"])
        self.assertNotIn("allow", permissions)
        self.assertNotIn("ask", permissions)

    def test_24_the_deny_list_is_derived_from_actions_yaml(self):
        self.assertEqual(self.config["permissions"]["deny"], policy_rules.codex_deny_rules())

    def test_25_every_agent_gates_every_tool_call_and_blocks_on_hook_error(self):
        for name, agent in sorted(self.agents.items()):
            with self.subTest(agent=name):
                entries = agent["hooks"]["pre_tool_use"]
                self.assertEqual(len(entries), 1)
                self.assertEqual(entries[0]["matcher"], "*")
                hooks = entries[0]["hooks"]
                self.assertEqual(len(hooks), 1)
                self.assertEqual(hooks[0]["type"], "command")
                self.assertEqual(hooks[0]["command"], "/opt/dca/bin/dca-gate")
                self.assertEqual(hooks[0]["on_error"], "block")

    def test_26_every_agent_records_fingerprints_on_switch_and_subagent_stop(self):
        for name, agent in sorted(self.agents.items()):
            with self.subTest(agent=name):
                hooks = agent["hooks"]
                for event in ("on_agent_switch", "subagent_stop"):
                    self.assertEqual(hooks[event][0]["command"], "/opt/dca/bin/dca-fingerprint")

    # --- effective capabilities ------------------------------------------------------------

    def test_30_root_has_exactly_a_writable_filesystem_and_a_shell(self):
        toolsets = self.agents["root"]["toolsets"]
        self.assertEqual(sorted(t["type"] for t in toolsets), ["filesystem", "shell"])
        filesystem = next(t for t in toolsets if t["type"] == "filesystem")
        self.assertNotIn("readonly", filesystem)

    def test_31_root_delegates_only_to_the_researcher_and_the_reviewer(self):
        self.assertEqual(self.agents["root"]["sub_agents"], ["researcher", "reviewer"])

    def test_32_root_lists_exactly_the_four_runtime_skills(self):
        self.assertEqual(sorted(self.agents["root"]["skills"]),
                         ["change-receipt", "repository-navigation", "root-cause-debugging",
                          "verification"])

    def test_33_only_root_has_sub_agents_or_skills(self):
        for name in ("researcher", "reviewer"):
            with self.subTest(agent=name):
                self.assertNotIn("sub_agents", self.agents[name])
                self.assertNotIn("skills", self.agents[name])

    def test_34_the_researcher_is_readonly_with_a_read_only_filesystem_only(self):
        researcher = self.agents["researcher"]
        self.assertIs(researcher["readonly"], True)
        self.assertEqual(researcher["toolsets"], [{"type": "filesystem", "readonly": True}])

    def test_35_the_reviewer_has_no_agent_level_readonly(self):
        # The flag would filter out every tool without a read-only annotation, which removes the
        # script tools the review depends on (research E18).
        self.assertNotIn("readonly", self.agents["reviewer"])

    def test_36_the_reviewer_has_a_read_only_filesystem_and_exactly_three_fixed_git_commands(self):
        toolsets = self.agents["reviewer"]["toolsets"]
        self.assertEqual(sorted(t["type"] for t in toolsets), ["filesystem", "script"])
        filesystem = next(t for t in toolsets if t["type"] == "filesystem")
        self.assertIs(filesystem["readonly"], True)
        script = next(t for t in toolsets if t["type"] == "script")
        self.assertEqual(sorted(script["shell"]), ["git_diff", "git_log", "git_status"])
        for name, definition in sorted(script["shell"].items()):
            with self.subTest(command=name):
                self.assertTrue(definition["cmd"].startswith("git "))
                self.assertNotIn("args", definition)
                self.assertNotIn("required", definition)

    def test_37_the_reviewer_has_no_shell_and_no_writable_filesystem(self):
        types = [t["type"] for t in self.agents["reviewer"]["toolsets"]]
        self.assertNotIn("shell", types)
        for toolset in self.agents["reviewer"]["toolsets"]:
            if toolset["type"] == "filesystem":
                self.assertIs(toolset["readonly"], True)

    def test_38_no_agent_declares_a_broader_toolset(self):
        forbidden = {"fetch", "open_url", "api", "mcp", "mcp_catalog", "rag", "memory", "a2a",
                     "webhook", "background_agents", "scheduler", "open_url", "model_picker"}
        for name, agent in sorted(self.agents.items()):
            for toolset in agent.get("toolsets") or []:
                with self.subTest(agent=name, toolset=toolset["type"]):
                    self.assertNotIn(toolset["type"], forbidden)

    # --- native ceilings --------------------------------------------------------------------

    def test_40_the_budget_is_top_level_and_run_wide(self):
        self.assertEqual(self.config["budget"]["max_tokens"],
                         LIMITS["native_ceilings"]["max_tokens"])
        self.assertNotIn("budgets", self.config)

    def test_41_no_agent_declares_its_own_budget(self):
        for name, agent in sorted(self.agents.items()):
            with self.subTest(agent=name):
                self.assertNotIn("budgets", agent)
                self.assertNotIn("budget", agent)

    def test_42_every_agent_carries_the_native_iteration_ceilings(self):
        ceilings = LIMITS["native_ceilings"]
        for name, agent in sorted(self.agents.items()):
            with self.subTest(agent=name):
                self.assertEqual(agent["max_iterations"], ceilings["max_iterations"])
                self.assertEqual(agent["max_consecutive_tool_calls"],
                                 ceilings["max_consecutive_tool_calls"])

    def test_43_there_are_no_flavors_and_no_second_config(self):
        self.assertNotIn("flavors", self.config)
        self.assertEqual(sorted(p.name for p in AGENTS.glob("codex*.yaml")), ["codex.yaml"])

    def test_44_context_management_values_are_pinned(self):
        for name, agent in sorted(self.agents.items()):
            with self.subTest(agent=name):
                self.assertEqual(agent["max_tool_result_tokens"], 8000)
                self.assertEqual(agent["max_old_tool_call_tokens"], 40000)
                self.assertIs(agent["session_compaction"], True)
                self.assertEqual(agent["compaction_threshold"], 0.8)

    # --- excluded V1 features ----------------------------------------------------------------

    def test_45_there_is_no_code_mode_mcp_or_rag(self):
        for key in ("mcps", "rag", "code_mode_tools", "models", "providers"):
            with self.subTest(key=key):
                self.assertNotIn(key, self.config)
        for name, agent in sorted(self.agents.items()):
            with self.subTest(agent=name):
                self.assertNotIn("code_mode_tools", agent)

    def test_46_every_agent_names_its_instruction_file_relatively(self):
        # The pinned v15 parser refuses an absolute path or one outside the config's directory.
        for name, agent in sorted(self.agents.items()):
            with self.subTest(agent=name):
                path = agent["instruction_file"]
                self.assertFalse(path.startswith("/"))
                self.assertNotIn("..", path)
                self.assertEqual(path, f"instructions/{name}.md")


class TestUnavailableBackendsAreReported(unittest.TestCase):
    def test_50_every_backend_is_either_checked_or_skipped_with_a_reason(self):
        for backend in ("claude", "codex"):
            with self.subTest(backend=backend):
                available, reason = availability(backend)
                if available:
                    self.assertTrue((AGENTS / f"{backend}.yaml").is_file())
                else:
                    self.assertTrue(reason, "an unavailable backend must record why")


if __name__ == "__main__":
    unittest.main()
