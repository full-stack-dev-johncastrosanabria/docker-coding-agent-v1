"""Model, effort and autocompact chosen per run (`dca run/bench --model --effort --autocompact`).

The property that matters most: with no choice the staged kit is byte-identical to the one the
gates ran against. A choice is validated on the host and lands only where each backend reads it.
"""

import importlib.util
import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


errors = _load("dca_errors", ROOT / "src" / "dca" / "errors.py")
launcher = _load("dca_launcher", ROOT / "src" / "dca" / "launcher.py")
stage = _load("dca_stage_choice", ROOT / "runtime" / "sandbox" / "kit" / "stage.py")
CONFIG = {b: (ROOT / "runtime" / "agents" / f"{b}.yaml").read_text() for b in ("claude", "codex")}
SCHEMA = json.loads((ROOT / "specs" / "001-bounded-coding-agent" / "contracts" /
                     "completion-report.schema.json").read_text())


class Validation(unittest.TestCase):
    def test_01_nothing_chosen_is_an_empty_choice(self):
        self.assertEqual(launcher.agent_choice("claude"), {})

    def test_02_bad_values_are_usage_errors(self):
        for backend, kwargs in (("claude", {"model": "x\nmodel: evil"}),
                                ("claude", {"model": "-rf"}),
                                ("claude", {"effort": "none"}),     # codex-only level
                                ("codex", {"effort": "adaptive"}),
                                ("codex", {"autocompact": "100"}),
                                ("codex", {"autocompact": "0"}),
                                ("codex", {"autocompact": "maybe"})):
            with self.subTest(backend=backend, **kwargs):
                with self.assertRaises(Exception) as caught:
                    launcher.agent_choice(backend, **kwargs)
                self.assertEqual(type(caught.exception).__name__, "UsageError")

    def test_03_a_valid_choice_is_what_the_report_schema_accepts(self):
        choice = launcher.agent_choice("codex", model="chatgpt/gpt-6.1-sol", effort="xhigh",
                                       autocompact="OFF")
        self.assertEqual(choice["autocompact"], "off")
        requested = SCHEMA["properties"]["versions"]["properties"]["requested"]
        stdlib = _load("dca_jsonschema", ROOT / "src" / "dca" / "jsonschema.py")
        self.assertEqual(stdlib.validate(choice, requested), [])
        self.assertNotEqual(stdlib.validate(dict(choice, extra="x"), requested), [])


class Rendering(unittest.TestCase):
    def test_04_no_choice_leaves_both_configs_byte_identical(self):
        for backend, text in CONFIG.items():
            self.assertEqual(stage.render_agent_config(backend, text, {}), text)
        self.assertEqual(stage.claude_compaction_env({}), {})

    def test_05_claude_gets_harness_model_and_effort_and_managed_compaction_env(self):
        text = stage.render_agent_config("claude", CONFIG["claude"],
                                         {"model": "claude-opus-5-5", "effort": "max"})
        self.assertIn('      model: "claude-opus-5-5"\n', text)
        self.assertIn('      effort: "max"\n', text)
        self.assertEqual(stage.claude_compaction_env({"autocompact": "off"}),
                         {"DISABLE_AUTO_COMPACT": "1"})
        self.assertEqual(stage.claude_compaction_env({"autocompact": "60"}),
                         {"CLAUDE_AUTOCOMPACT_PCT_OVERRIDE": "60"})

    def test_06_codex_every_agent_uses_one_named_model_with_the_effort(self):
        text = stage.render_agent_config("codex", CONFIG["codex"],
                                         {"model": "gpt-6.1-sol", "effort": "low",
                                          "autocompact": "off"})
        self.assertIn('models:\n  dca:\n    provider: "chatgpt"\n    model: "gpt-6.1-sol"\n'
                      '    thinking_budget: "low"\n', text)
        self.assertEqual(text.count("    model: dca\n"), CONFIG["codex"].count("model: chatgpt/"))
        self.assertNotIn("session_compaction: true", text)
        threshold = stage.render_agent_config("codex", CONFIG["codex"], {"autocompact": "70"})
        self.assertIn("compaction_threshold: 0.7\n", threshold)
        self.assertNotIn("compaction_threshold: 0.8", threshold)

    def test_07_a_config_without_the_line_refuses_instead_of_ignoring_the_choice(self):
        with self.assertRaises(stage.StagingError):
            stage.render_agent_config("claude", "agents: {}\n", {"effort": "low"})


if __name__ == "__main__":
    unittest.main()
