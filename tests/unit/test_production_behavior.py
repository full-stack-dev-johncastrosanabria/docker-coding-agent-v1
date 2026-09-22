"""T062 behaviour harness: pairing reviewer sessions from each backend's fingerprint records."""

import importlib.util
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[2]


def _load(name, path):
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


behavior = _load("dca_production_behavior", ROOT / "gates" / "production" / "behavior.py")


def record(agent, event, fingerprint):
    return {"agent": agent, "event": event, "fingerprint": fingerprint}


class ReviewerSessions(unittest.TestCase):
    def test_codex_session_is_bracketed_by_root_switches(self):
        records = [
            record("root", "on_agent_switch", "sha256:a"),
            record("researcher", "subagent_stop", "sha256:a"),
            record("root", "on_agent_switch", "sha256:a"),
            record("root", "on_agent_switch", "sha256:b"),
            record("reviewer", "subagent_stop", "sha256:b"),
            record("root", "on_agent_switch", "sha256:b"),
        ]
        self.assertEqual(behavior.reviewer_sessions(records),
                         [["sha256:b", "sha256:b", "sha256:b"]])

    def test_claude_session_pairs_start_and_stop(self):
        # The shape the managed SubagentStart/SubagentStop hooks record. Before this pairing
        # existed the check saw zero reviewer sessions for Claude and failed on a clean run.
        records = [
            record("dca-researcher", "SubagentStart", "sha256:a"),
            record("dca-researcher", "SubagentStop", "sha256:a"),
            record("dca-reviewer", "SubagentStart", "sha256:b"),
            record("dca-reviewer", "SubagentStop", "sha256:b"),
            record("root", "stop", "sha256:b"),
        ]
        self.assertEqual(behavior.reviewer_sessions(records), [["sha256:b", "sha256:b"]])

    def test_claude_reviewer_mutation_shows_two_fingerprints(self):
        records = [
            record("dca-reviewer", "SubagentStart", "sha256:b"),
            record("dca-reviewer", "SubagentStop", "sha256:c"),
        ]
        self.assertEqual(behavior.reviewer_sessions(records), [["sha256:b", "sha256:c"]])

    def test_missing_start_is_recorded_as_none(self):
        records = [record("dca-reviewer", "SubagentStop", "sha256:b")]
        self.assertEqual(behavior.reviewer_sessions(records), [[None, "sha256:b"]])

    def test_start_of_another_agent_does_not_bracket_the_reviewer(self):
        records = [
            record("dca-researcher", "SubagentStart", "sha256:a"),
            record("dca-reviewer", "SubagentStop", "sha256:a"),
        ]
        self.assertEqual(behavior.reviewer_sessions(records), [[None, "sha256:a"]])


if __name__ == "__main__":
    unittest.main()
