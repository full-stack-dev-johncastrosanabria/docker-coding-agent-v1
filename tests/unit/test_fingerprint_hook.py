"""The managed fingerprint hook's payload reading (tasks.md T083; FR-022).

Standard library only. These tests exist because a review caught a real gap by reading the vendor
schema rather than the code: the hook resolved the agent from `agent_name`/`agent_type`/
`subagent_type`/`agent`, and a SWITCH event carries none of them. Every switch record therefore
resolved to "unknown", so one backend had a reviewer STOP with no reviewer START, the host could
never pair the two, and every planned run on that backend would have been unprovable - the same
over-blocking failure the host-authoritative change exists to remove, moved to the other backend.

The payload shapes here are taken from the vendor schema this repository already pins
(tests/contract/agent-schema-v1.136.0.json), quoted in the assertions, so the fixtures cannot drift
from the contract silently: `subagent_stop` documents "The sub-agent's name is in agent_name", and
`on_agent_switch` documents "Receives from_agent, to_agent, and agent_switch_kind in the input".
"""

import importlib.util
import json
import os
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCHEMA = ROOT / "tests" / "contract" / "agent-schema-v1.136.0.json"


def load_hook():
    """The shipped hook, loaded by path exactly as the VM loads it."""
    path = ROOT / "src" / "dca" / "fingerprint_hook.py"
    spec = importlib.util.spec_from_file_location("dca_fingerprint_hook_under_test", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


hook = load_hook()
fingerprint_spec = importlib.util.spec_from_file_location(
    "dca_fingerprint_for_hook_tests", ROOT / "src" / "dca" / "fingerprint.py")
fingerprint = importlib.util.module_from_spec(fingerprint_spec)
sys.modules[fingerprint_spec.name] = fingerprint
fingerprint_spec.loader.exec_module(fingerprint)

DIGEST = "sha256:" + "7" * 64


class TestPayloadShapesMatchTheVendorSchema(unittest.TestCase):
    """The fixtures below are only meaningful while the schema still says what they assume."""

    def setUp(self):
        self.descriptions = json.loads(SCHEMA.read_text(encoding="utf-8"))[
            "definitions"]["HooksConfig"]["properties"]

    def test_01_subagent_stop_still_documents_agent_name(self):
        self.assertIn("agent_name", self.descriptions["subagent_stop"]["description"])

    def test_02_on_agent_switch_still_documents_to_agent_and_not_agent_name(self):
        text = self.descriptions["on_agent_switch"]["description"]
        self.assertIn("to_agent", text)
        self.assertIn("from_agent", text)
        self.assertNotIn("agent_name", text,
                         "a switch payload naming agent_name would make the to_agent fallback moot")


class TestAgentAttribution(unittest.TestCase):
    def resolved(self, payload):
        return hook._event_and_agent(payload)

    # --- the gap that was found --------------------------------------------------------------

    def test_10_a_switch_payload_that_names_only_the_entered_agent_is_still_attributed(self):
        """The `to_agent` fallback. A live run shows the payload usually ALSO carries the executing
        agent, which wins - so this covers the documented-schema-only shape, not the observed one."""
        event, agent = self.resolved({"hook_event_name": "on_agent_switch", "from_agent": "root",
                                      "to_agent": "reviewer", "agent_switch_kind": "transfer_task"})
        self.assertEqual(event, "on_agent_switch")
        self.assertEqual(agent, "reviewer")

    def test_11_from_agent_is_never_read(self):
        """It names the agent being LEFT, so reading it would attribute a record to the delegator."""
        _, agent = self.resolved({"hook_event_name": "on_agent_switch", "from_agent": "reviewer",
                                  "to_agent": "root", "agent_switch_kind": "return"})
        self.assertEqual(agent, "root")

    def test_11a_the_executing_agent_outranks_the_entered_one(self):
        """Observed live: a switch carries `agent: root` as well, and that is what the record gets."""
        _, agent = self.resolved({"hook_event_name": "on_agent_switch", "agent": "root",
                                  "from_agent": "root", "to_agent": "reviewer"})
        self.assertEqual(agent, "root")

    def test_12_a_subagent_stop_is_attributed_from_agent_name(self):
        _, agent = self.resolved({"hook_event_name": "subagent_stop", "agent_name": "reviewer",
                                  "stop_response": "Verdict: supports success"})
        self.assertEqual(agent, "reviewer")

    def test_13_an_explicit_name_outranks_the_switch_fallback(self):
        _, agent = self.resolved({"hook_event_name": "on_agent_switch", "agent_name": "reviewer",
                                  "to_agent": "someone-else"})
        self.assertEqual(agent, "reviewer")

    def test_14_the_native_subagent_events_are_attributed_from_agent_type(self):
        for key in ("agent_type", "subagent_type", "agent_name", "agent"):
            with self.subTest(key=key):
                _, agent = self.resolved({"hook_event_name": "SubagentStart", key: "reviewer"})
                self.assertEqual(agent, "reviewer")

    def test_15_a_payload_naming_nobody_is_unknown_not_guessed(self):
        for payload in ({}, {"hook_event_name": "SubagentStop"},
                        {"hook_event_name": "on_agent_switch", "from_agent": "root"}):
            with self.subTest(payload=sorted(payload)):
                _, agent = self.resolved(payload)
                self.assertEqual(agent, "unknown")


class TestEndToEndPairing(unittest.TestCase):
    """A realistic delegation sequence per backend must yield a PROVEN identity, and both must
    behave the same way: this is the parity the host-side FR-022 path depends on."""

    def record(self, payload, digest=DIGEST):
        event, agent = hook._event_and_agent(payload)
        return {"ts": "2026-09-26T00:00:00Z", "event": event, "agent": agent,
                "fingerprint": digest, "error": None}

    #: Exactly the record a live Codex run produced (gates/production/work/behavior-codex,
    #: 2026-09-26): the switch records are attributed to ROOT - the payload names the executing
    #: agent - and the reviewer's only record is its own `subagent_stop`.
    def codex_sequence(self, before=DIGEST, after=DIGEST):
        """root delegates to the researcher, returns, delegates to the reviewer, returns."""
        early = "sha256:" + "1" * 64
        return [
            self.record({"hook_event_name": "on_agent_switch", "agent": "root",
                         "from_agent": "root", "to_agent": "researcher"}, early),
            self.record({"hook_event_name": "subagent_stop", "agent_name": "researcher"}, early),
            self.record({"hook_event_name": "on_agent_switch", "agent": "root",
                         "from_agent": "researcher", "to_agent": "root"}, early),
            self.record({"hook_event_name": "on_agent_switch", "agent": "root",
                         "from_agent": "root", "to_agent": "reviewer"}, before),
            self.record({"hook_event_name": "subagent_stop", "agent_name": "reviewer"}, after),
            self.record({"hook_event_name": "on_agent_switch", "agent": "root",
                         "from_agent": "reviewer", "to_agent": "root"}, after),
        ]

    def claude_sequence(self, before=DIGEST, after=DIGEST):
        return [
            self.record({"hook_event_name": "SubagentStart", "agent_type": "researcher"},
                        "sha256:" + "1" * 64),
            self.record({"hook_event_name": "SubagentStop", "agent_type": "researcher"},
                        "sha256:" + "1" * 64),
            self.record({"hook_event_name": "SubagentStart", "agent_type": "reviewer"}, before),
            self.record({"hook_event_name": "SubagentStop", "agent_type": "reviewer"}, after),
        ]

    def test_20_both_backends_prove_identity_on_an_unchanged_candidate(self):
        for name, records in (("codex", self.codex_sequence()), ("claude", self.claude_sequence())):
            with self.subTest(backend=name):
                verdict = fingerprint.review_identity(records)
                self.assertTrue(verdict["proven"], verdict["reason"])
                self.assertEqual(verdict["agent"], "reviewer")

    def test_21_both_backends_detect_a_candidate_that_changed(self):
        changed = "sha256:" + "9" * 64
        for name, records in (("codex", self.codex_sequence(after=changed)),
                              ("claude", self.claude_sequence(after=changed))):
            with self.subTest(backend=name):
                verdict = fingerprint.review_identity(records)
                self.assertFalse(verdict["proven"])
                self.assertTrue(verdict["mismatch"])

    def test_22_the_researchers_earlier_session_never_stands_in(self):
        """The researcher ran before the edits, so its fingerprints differ and must be ignored."""
        for name, records in (("codex", self.codex_sequence()), ("claude", self.claude_sequence())):
            with self.subTest(backend=name):
                verdict = fingerprint.review_identity(records)
                self.assertEqual(verdict["fingerprint_before"], DIGEST)
                self.assertNotEqual(verdict["fingerprint_before"], "sha256:" + "1" * 64)

    def test_24_the_verdict_matches_a_record_a_live_run_actually_produced(self):
        """Ground truth, not a guess: the record retained by the T062 behaviour gate."""
        captured = (ROOT / "gates" / "production" / "work" / "behavior-codex" /
                    "fingerprints.json")
        if not captured.is_file():
            self.skipTest("no captured behaviour record on this machine")
        records = json.loads(captured.read_text(encoding="utf-8"))
        verdict = fingerprint.review_identity(records)
        self.assertTrue(verdict["proven"], verdict["reason"])
        self.assertEqual(verdict["agent"], "reviewer")
        self.assertEqual(verdict["fingerprint_before"], verdict["fingerprint_after"])

    def test_23_the_roots_own_run_record_hooks_do_not_enter_the_comparison(self):
        records = self.claude_sequence() + [
            self.record({"hook_event_name": "turn_end", "agent": "root"}, "sha256:" + "5" * 64),
            self.record({"hook_event_name": "stop", "agent": "root"}, "sha256:" + "6" * 64)]
        verdict = fingerprint.review_identity(records)
        self.assertTrue(verdict["proven"], "root's own record hooks must not disturb the verdict")


if __name__ == "__main__":
    unittest.main()
