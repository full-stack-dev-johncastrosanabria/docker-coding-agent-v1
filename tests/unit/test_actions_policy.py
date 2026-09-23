"""Unit tests for the action-class policy data (tasks.md T025, runtime/policy/actions.yaml).

Table-driven and verbatim against data-model.md "Action Classes". The policy file is DATA: the gate
reads it and decides from it, so an error here is a silent policy change rather than a crash. The
table below is transcribed from data-model.md and is the thing under test - if the two ever
disagree, one of them is wrong and the gate is deciding something nobody specified.

The file is JSON-encoded even though it is named .yaml, matching runtime/versions.yaml. That is
forced by the contract, not a preference: the gate runs under `python3 -I` with sys.path restricted
to its trusted root plus the stdlib (T030), so PyYAML is not importable there and the policy must be
parseable with the standard library alone.
"""

import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
POLICY = ROOT / "runtime" / "policy" / "actions.yaml"

ALLOW, ASK, DENY = "ALLOW", "ASK", "DENY"

# (class id, name fragment, trusted decision, untrusted decision) transcribed from data-model.md.
# Only classes 14, 17 and 31 differ by trust level; every other row is the same for both.
EXPECTED = (
    (1, "Read workspace file", ALLOW, ALLOW),
    (2, "excluded-sensitive", DENY, DENY),
    (3, "in-scope source", ALLOW, ALLOW),
    (4, "unrelated to task scope", ASK, ASK),
    (5, "outside workspace", DENY, DENY),
    (6, "scratch", ALLOW, ALLOW),
    (7, "generated files", ALLOW, ALLOW),
    (8, "verification command", ALLOW, ALLOW),
    (9, "Formatter", ALLOW, ALLOW),
    (10, "Delete tracked", ALLOW, ALLOW),
    (11, "Delete untracked", ASK, ASK),
    (12, "Repository script", ASK, ASK),
    (13, "isolated environment", ASK, ASK),
    (14, "repository-declared dependencies", ALLOW, ASK),
    (15, "new dependency", ASK, ASK),
    (16, "Install onto host", DENY, DENY),
    (17, "Source-control read", ALLOW, ASK),
    (18, "Source-control write", ASK, ASK),
    (19, "Force-push", DENY, DENY),
    (20, "own unpublished task branch", ASK, ASK),
    (21, "External API call", ASK, ASK),
    (22, "Deployment", DENY, DENY),
    (23, "Credential", DENY, DENY),
    (24, "disposable resource", ALLOW, ALLOW),
    (25, "own policy", DENY, DENY),
    (26, "skill not in runtime allowlist", DENY, DENY),
    (27, "mutating tool by researcher/reviewer", DENY, DENY),
    (28, "Unparseable", ASK, ASK),
    (29, "after step limit", DENY, DENY),
    (30, "web browsing", DENY, DENY),
    (31, "documentation", ALLOW, ASK),
)

# The approval-grant schema's non-grantable list. A grant can never turn one of these into an ALLOW,
# so this set is a security boundary rather than a convenience.
DENY_SET = {2, 5, 16, 19, 22, 23, 25, 26, 27, 29, 30}

RUNTIME_SKILLS = ("repository-navigation", "root-cause-debugging", "verification",
                  "change-receipt")


def load_policy():
    with open(POLICY, encoding="utf-8") as fh:
        return json.load(fh)


class ActionsPolicyShape(unittest.TestCase):
    def setUp(self):
        self.policy = load_policy()
        self.classes = {c["id"]: c for c in self.policy["classes"]}

    def test_01_the_policy_parses_with_the_standard_library_alone(self):
        """The gate cannot import PyYAML, so the policy must be stdlib-parseable."""
        self.assertIsInstance(self.policy, dict)
        self.assertEqual(self.policy["version"], 1)

    def test_02_exactly_classes_1_through_31_are_defined(self):
        self.assertEqual(sorted(self.classes), list(range(1, 32)))
        self.assertEqual(len(self.policy["classes"]), 31, "no duplicate class ids")

    def test_03_every_class_has_exactly_one_decision_per_trust_level(self):
        for identifier, entry in sorted(self.classes.items()):
            with self.subTest(action_class=identifier):
                decision = entry["decision"]
                self.assertEqual(sorted(decision), ["trusted", "untrusted"])
                for level in ("trusted", "untrusted"):
                    self.assertIn(decision[level], (ALLOW, ASK, DENY))

    def test_04_every_decision_matches_data_model_verbatim(self):
        for identifier, fragment, trusted, untrusted in EXPECTED:
            with self.subTest(action_class=identifier):
                entry = self.classes[identifier]
                self.assertIn(fragment.lower(), entry["name"].lower(),
                              f"class {identifier} name drifted from data-model.md")
                self.assertEqual(entry["decision"]["trusted"], trusted)
                self.assertEqual(entry["decision"]["untrusted"], untrusted)

    def test_05_the_named_examples_from_the_task_hold(self):
        self.assertEqual(self.classes[14]["decision"], {"trusted": ALLOW, "untrusted": ASK})
        self.assertEqual(self.classes[21]["decision"]["trusted"], ASK)
        self.assertEqual(self.classes[21]["decision"]["untrusted"], ASK)
        self.assertEqual(self.classes[30]["decision"]["trusted"], DENY)
        self.assertEqual(self.classes[30]["decision"]["untrusted"], DENY)
        self.assertEqual(self.classes[31]["decision"], {"trusted": ALLOW, "untrusted": ASK})

    def test_06_the_deny_set_is_exactly_the_non_grantable_list(self):
        actual = {i for i, e in self.classes.items()
                  if e["decision"]["trusted"] == DENY or e["decision"]["untrusted"] == DENY}
        self.assertEqual(actual, DENY_SET)

    def test_07_a_deny_class_denies_at_both_trust_levels(self):
        """Trust level changes only the network rows and the isolation profile, never a DENY."""
        for identifier in sorted(DENY_SET):
            with self.subTest(action_class=identifier):
                self.assertEqual(self.classes[identifier]["decision"],
                                 {"trusted": DENY, "untrusted": DENY})

    def test_08_only_the_network_rows_differ_by_trust_level(self):
        differing = {i for i, e in self.classes.items()
                     if e["decision"]["trusted"] != e["decision"]["untrusted"]}
        self.assertEqual(differing, {14, 17, 31})

    def test_09_every_class_records_its_source_requirement(self):
        for identifier, entry in sorted(self.classes.items()):
            with self.subTest(action_class=identifier):
                self.assertTrue(entry.get("source"), "class must cite its FR/research source")


class ActionsPolicyMetadata(unittest.TestCase):
    """T026's metadata: path rules, tool matchers, network intent and the class-26 complement."""

    def setUp(self):
        self.policy = load_policy()
        self.classes = {c["id"]: c for c in self.policy["classes"]}

    def test_10_sensitive_globs_are_defined_and_cover_credential_paths(self):
        globs = self.policy["policy"]["sensitive_globs"]
        self.assertTrue(globs)
        joined = " ".join(globs)
        for expected in (".env", ".ssh", "chatgpt-auth.json", ".credentials.json"):
            with self.subTest(glob=expected):
                self.assertIn(expected, joined)

    def test_11_network_intent_rules_exist_for_classes_21_30_and_31(self):
        for identifier in (21, 30, 31):
            with self.subTest(action_class=identifier):
                self.assertIn("network", self.classes[identifier])
        self.assertTrue(self.policy["policy"]["documentation_hosts"],
                        "class 31 needs the policy-listed documentation hosts")
        self.assertTrue(self.classes[30]["network"]["tools"],
                        "class 30 needs browsing/fetch tool matchers")

    def test_12_class_26_carries_its_positive_complement_allowlist(self):
        complement = self.classes[26]["positive_complement"]
        self.assertEqual(complement["delegation"]["from"], "root")
        self.assertEqual(sorted(complement["delegation"]["to"]), ["researcher", "reviewer"])
        self.assertEqual(complement["skills"]["from"], "root")
        self.assertEqual(sorted(complement["skills"]["names"]), sorted(RUNTIME_SKILLS))
        self.assertEqual(complement["skills"]["source"], "trusted-kit")

    def test_13_the_complement_matches_both_backends_delegation_tools(self):
        matchers = self.classes[26]["positive_complement"]["delegation"]["tools"]
        joined = json.dumps(matchers)
        self.assertIn("transfer_task", joined, "Codex delegation tool")
        self.assertIn("dca-researcher", joined, "Claude subagent")
        self.assertIn("dca-reviewer", joined, "Claude subagent")

    def test_14_the_complement_matches_both_backends_skill_tools(self):
        matchers = self.classes[26]["positive_complement"]["skills"]["tools"]
        joined = json.dumps(matchers)
        self.assertIn("read_skill", joined, "Codex skill tool")
        self.assertIn("Skill", joined, "Claude skill tool")

    def test_15_run_skill_is_never_on_the_allowlist(self):
        """A forked-skill run is class 26 DENY however it is spelled."""
        complement = json.dumps(self.classes[26]["positive_complement"])
        self.assertNotIn("run_skill", complement)
        self.assertIn("run_skill", json.dumps(self.classes[26].get("denied_tools", [])),
                      "run_skill must be named explicitly as denied")

    def test_16_path_rule_classes_declare_how_a_path_is_judged(self):
        for identifier in (1, 2, 5, 6):
            with self.subTest(action_class=identifier):
                self.assertIn("paths", self.classes[identifier])
        self.assertTrue(self.classes[5]["paths"]["realpath"],
                        "class 5 is decided on realpath, so symlink escape is caught")

    def test_17_mutating_tool_matchers_exist_for_class_27(self):
        entry = self.classes[27]
        self.assertTrue(entry["tools"]["mutating"])
        self.assertEqual(sorted(entry["agents"]), ["researcher", "reviewer"])
        # the Codex reviewer's fixed read-only git tools are inspection, not mutation
        read_only = json.dumps(entry["tools"].get("read_only_exempt", []))
        for tool in ("git_diff", "git_status", "git_log"):
            with self.subTest(tool=tool):
                self.assertIn(tool, read_only)


class TestNativeRulesNeverDenyScratchWrites(unittest.TestCase):
    """Regression (dca bench, Codex K1, now R1): a native deny rule broader than the gate.

    The gate decides the scratch dir first (class 6, ALLOW), and the root is REQUIRED to write
    context.json, plan.md and report.agent.json there. A native `/run/dca/**` rule rejected that
    write before the gate ran, and the run ended blocked. Native rules may be stricter than the gate
    only where the gate also denies; they must never reject what the gate is designed to allow.
    """

    SCRATCH_WRITES = ("/run/dca/out/context.json", "/run/dca/out/plan.md",
                      "/run/dca/out/report.agent.json", "/run/dca/out/notes/tmp.txt")
    LAUNCHER_FILES = ("/run/dca/run.json", "/run/dca/grants.json", "/run/dca/task.txt",
                      "/run/dca/state/gate.log.jsonl", "/run/dca/cagent/chatgpt-auth.json",
                      "/opt/dca/policy/actions.yaml", "/etc/claude-code/managed-settings.json")

    @classmethod
    def setUpClass(cls):
        import fnmatch
        import importlib.util
        spec = importlib.util.spec_from_file_location("dca_policy_rules_scratch",
                                                      ROOT / "src" / "dca" / "policy_rules.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        cls.fnmatch = staticmethod(fnmatch.fnmatchcase)
        cls.codex = [rule.split(":path=", 1) for rule in module.codex_deny_rules()
                     if ":path=" in rule]
        cls.claude = [rule[:-1].split("(", 1) for rule in module.claude_deny_rules()
                      if rule.endswith(")") and "(/" in rule]

    def denied(self, rules, tool, path):
        # `*` and `**` both match across separators here, which is the BROADEST reading of either
        # backend's glob syntax - so "not denied" below holds under the narrower readings too.
        return [glob for name, glob in rules if name == tool and self.fnmatch(path, glob)]

    def test_no_native_rule_rejects_a_scratch_write(self):
        for path in self.SCRATCH_WRITES:
            for tool in ("write_file", "edit_file", "create_directory"):
                with self.subTest(backend="codex", tool=tool, path=path):
                    self.assertEqual(self.denied(self.codex, tool, path), [])
            for tool in ("Write", "Edit"):
                with self.subTest(backend="claude", tool=tool, path=path):
                    self.assertEqual(self.denied(self.claude, tool, path), [])

    def test_the_launchers_own_files_stay_natively_denied(self):
        for path in self.LAUNCHER_FILES:
            with self.subTest(backend="codex", path=path):
                self.assertTrue(self.denied(self.codex, "write_file", path))
            with self.subTest(backend="claude", path=path):
                self.assertTrue(self.denied(self.claude, "Write", path))


if __name__ == "__main__":
    unittest.main()
