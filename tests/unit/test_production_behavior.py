"""T062 behaviour harness: reviewer-session pairing, and how a run's evidence is finalized."""

import contextlib
import importlib.util
import io
import json
from pathlib import Path
import shutil
import sys
import tempfile
import types
import unittest
from unittest import mock

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


class Finalization(unittest.TestCase):
    """The evidence document says PASS only when every probe actually ran."""

    def setUp(self):
        self.work = Path(tempfile.mkdtemp(prefix="dca-behavior-"))
        self.addCleanup(shutil.rmtree, self.work, ignore_errors=True)

    def run_main(self, probes):
        class Launcher:
            source_commit = "0" * 40

            def __init__(self, request):
                self.sandbox = None
                self.sbx = types.SimpleNamespace(list_sandboxes=lambda: [])

            def prepare_gate_run(self):
                pass

            def provision(self):
                self.sandbox = "dca-run-test"

            def execute_agent(self, timeout):
                analysis = types.SimpleNamespace(counters=dict, run_integrity=dict)
                return analysis, None, 0, ""

            def cleanup(self):
                self.sandbox = None

        stubs = {"WORK": self.work, "Launcher": Launcher,
                 "RunRequest": lambda **kw: types.SimpleNamespace(run_id="run-test", **kw),
                 "build_fixture": lambda backend: self.work / "fixture",
                 "run_gate_probes": probes, "sh": lambda *args: {"out": ""},
                 "assert_live_run": lambda *args: None,
                 "inspect_claude": lambda *args: {"ok": True, "loaded": {}},
                 "run_failclosed": lambda *args: None}
        for name, value in stubs.items():
            patcher = mock.patch.object(behavior, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        with mock.patch.object(sys, "argv", ["behavior.py", "--backend", "claude"]), \
                contextlib.redirect_stdout(io.StringIO()):
            try:
                behavior.main()
            except KeyboardInterrupt:
                pass
        return json.loads((self.work / "behavior-claude.json").read_text())

    def test_a_run_that_reaches_every_probe_can_pass(self):
        def probes(instance, backend, rows, out):
            behavior.checked(rows, "gate.decisions", True, {})

        document = self.run_main(probes)
        self.assertEqual(document["status"], "PASS")
        self.assertNotIn("harness.completed", [row["id"] for row in document["checks"]])

    def test_an_interrupted_run_is_written_as_fail_not_pass(self):
        def probes(instance, backend, rows, out):
            behavior.checked(rows, "gate.decisions", True, {})
            raise KeyboardInterrupt

        document = self.run_main(probes)
        self.assertEqual(document["status"], "FAIL")
        results = {row["id"]: row["result"] for row in document["checks"]}
        self.assertEqual(results["harness.completed"], "FAIL")
        self.assertEqual(results["cleanup.sandbox_removed"], "PASS")


if __name__ == "__main__":
    unittest.main()
