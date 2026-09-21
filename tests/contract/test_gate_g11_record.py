"""Contract tests for the G11 part A recorder (tasks.md T020, gates/G11/record.py).

Deterministic and offline: synthetic captures in a temporary directory, no sandbox, no sbx command,
no model call, and no change to the committed pins or to the accepted G1a/G3/G4 evidence.

G11-A decides whether a backend may be used at all, so the tests concentrate on the ways that
verdict could be dishonest:

  * part A reporting more than part A - the gate must stay PARTIAL and must never claim criteria 5-8;
  * a count that matches the stream but not what actually happened in the VM;
  * a spoof "resisted" by a capture that never contained the imitation;
  * a damaged or killed run being read as a successful one;
  * one backend's failure silently condemning or rescuing the other.
"""

import importlib.util
import json
import shutil
import tempfile
import unittest
from pathlib import Path

import jsonschema

ROOT = Path(__file__).resolve().parents[2]
GATES = ROOT / "gates"
STAMP = "2026-09-21T04:00:00Z"


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def ev(kind, **fields):
    return dict({"type": kind, "agent_name": "root", "timestamp": STAMP}, **fields)


def tool_call(identifier, name="Bash"):
    return ev("tool_call",
              tool_call={"id": identifier, "type": "function", "function": {"name": name}},
              tool_definition={"name": name, "description": "d"})


def stream(*events, terminal=True):
    body = list(events)
    if terminal:
        body.append(ev("stream_stopped", session_id="s1", reason="normal"))
    return "".join(json.dumps(e) + "\n" for e in body)


def run(*middle, terminal=True):
    return stream(
        ev("team_info", available_agents=[{"name": "root"}]),
        ev("user_message", message="task"),
        ev("stream_started", session_id="s1"),
        *middle,
        ev("token_usage", session_id="s1", usage={"input_tokens": 1, "output_tokens": 1}),
        terminal=terminal)


class G11Recorder(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.rec = _load("g11_record", GATES / "G11" / "record.py")
        cls.pp = cls.rec.pp
        cls.claude = cls.rec.claude
        cls.schema = json.loads((GATES / "evidence.schema.json").read_text(encoding="utf-8"))
        cls.versions = json.loads((ROOT / "runtime" / "versions.yaml").read_text(encoding="utf-8"))

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.work = self.tmp / "work"
        self.work.mkdir()
        self.captures = self.tmp / "captures"
        original = self.rec.CAPTURES
        self.rec.CAPTURES = str(self.captures)
        self.addCleanup(setattr, self.rec, "CAPTURES", original)
        self._write_work()
        for backend in ("claude", "codex"):
            self._write_captures(backend)

    # --- fixtures -------------------------------------------------------------------------------

    def _write(self, name, document):
        (self.work / name).write_text(
            document if isinstance(document, str) else json.dumps(document), encoding="utf-8")

    def _write_captures(self, backend, tool_calls=3, spoof_present=True, spoof_counted=False):
        out = self.captures / backend
        out.mkdir(parents=True, exist_ok=True)
        (out / "count.jsonl").write_text(
            run(*[tool_call(f"call_{i}") for i in range(tool_calls)]), encoding="utf-8")

        payload = ((GATES / "G11" / "spoof-lines.txt").read_text(encoding="utf-8")
                   if spoof_present else "nothing to see here")
        spoof_events = [tool_call("genuine_1"),
                        ev("tool_call_response", tool_call_id="genuine_1", response=payload,
                           result=payload, tool_definition={"name": "Bash"})]
        if spoof_counted:
            # what a parser that promoted printed JSON to an outer event would have produced
            spoof_events.append(tool_call(self.rec.SPOOF_CALL_ID))
        (out / "spoof.jsonl").write_text(run(*spoof_events), encoding="utf-8")

        # a killed run: the stream simply stops, with no terminal event
        (out / "kill.jsonl").write_text(run(tool_call("call_0"), terminal=False), encoding="utf-8")
        status, message = self.rec.derive(str(out))
        self.assertEqual(status, 0, message)

    def _write_work(self):
        pins = self.versions
        self._write("pf-version.json", {
            "client": {"version": pins["sbx"]["exact"]},
            "server": {"version": pins["sbx"]["exact"], "state": "running"}})
        self._write("pf-ssh-forwarding.json", {"key": "ssh.agentForwardingEnabled", "value": False})
        self._write("pf-ssh-socket.json", {"key": "ssh.agentSocketPath", "value": ""})
        baseline = {"rules": [dict(self.claude.preflight_module.BOOTSTRAP_RULE,
                                   name="default-deny-all", editable=False)]}
        accepted, why = self.claude.accepted_network_state(versions=self.versions)
        self.assertIsNone(why, why)
        self.assertEqual(self.claude.fingerprint(baseline, False), accepted)
        self._write("pf-policy.json", baseline)
        self._write("policy-after.json", baseline)
        self._write("pf-ls.json", {"sandboxes": []})
        self._write("ls-after.json", {"sandboxes": []})
        governance = {"allowed": False, "deny_kind": "implicit",
                      "resource_value": self.claude.GOVERNANCE_PROBE,
                      "target": self.claude.GOVERNANCE_PROBE, "governance": {"active": False}}
        self._write("governance-before.json", governance)
        self._write("governance-after.json", governance)

        images = []
        for module in (self.claude, self.rec.codex):
            image, digest = (module.claude_base(self.versions) if module is self.claude
                             else module.codex_base(self.versions))
            repository, _, tag = image.rpartition(":")
            images.append({"repository": f"docker.io/{repository}", "tag": tag,
                           "id": digest.removeprefix("sha256:")[:12]})
        self._write("templates.json", {"images": images})

        for backend in ("claude", "codex"):
            self._write(f"vmstate-{backend}.txt",
                        f"stdin_is_tty=no\nagent_binary={self.versions['docker_agent']}\n")
            self._write(f"markers-{backend}.txt", "markers=3\n")
            self._write(f"ls-before-{backend}.json", {"sandboxes": []})
            self._write(f"ls-between-{backend}.json", {"sandboxes": []})

    def _capture(self, **overrides):
        obs = {
            "sbx_env_ssh_auth_sock": "removed",
            "pf_version_exit": "0", "pf_ssh_forwarding_exit": "0", "pf_ssh_socket_exit": "0",
            "pf_policy_exit": "0", "pf_ls_exit": "0",
            "governance_before_exit": "1", "governance_after_exit": "1",
            "ls_after_exit": "0", "policy_after_exit": "0", "templates_exit": "0",
            "available_backends": "claude codex", "expected_tool_calls": "3",
        }
        for backend, module in (("claude", self.claude), ("codex", self.rec.codex)):
            allow, deny = (module.claude_policy("trusted") if backend == "claude"
                           else module.codex_policy("trusted"))
            image = (module.claude_base(self.versions)[0] if backend == "claude"
                     else module.codex_base(self.versions)[0])
            obs.update({
                f"sbx_resolved_base_{backend}": image,
                f"policy_allow_{backend}": ",".join(allow),
                f"policy_deny_{backend}": ",".join(deny),
                f"policy_rules_{backend}_exit": "0",
                f"safety_flag_{backend}": "--safety strict" if backend == "codex" else "",
                f"create_dca-g11-{backend}_exit": "0",
                f"rm_dca-g11-{backend}_exit": "0",
                f"count_{backend}_exit": "0", f"spoof_{backend}_exit": "0",
                f"vmstate_{backend}_exit": "0", f"markers_{backend}_exit": "0",
                f"kill_{backend}_stream_started": "1", f"kill_{backend}_signal_exit": "0",
                f"kill_{backend}_agent_exit": "137",
            })
        obs.update(overrides)
        return obs

    def _record_to(self, obs=None):
        obs = obs or self._capture()
        obs_path = self.tmp / "observations.env"
        obs_path.write_text("".join(f"{k}={v}\n" for k, v in obs.items()), encoding="utf-8")
        evidence_path = self.tmp / "G11.json"
        evidence, facts = self.rec.record(
            str(obs_path), str(self.work), str(ROOT / "runtime" / "versions.yaml"),
            str(evidence_path))
        return evidence, facts

    def _results(self, obs=None, backend="claude"):
        _, per_backend, statuses, _, _ = self.rec.evaluate(
            obs or self._capture(), str(self.work), self.versions)
        return ({row["id"]: row["result"] for row in per_backend[backend]},
                statuses[backend][0])

    def _validate(self, evidence):
        self.assertEqual(
            [e.message for e in
             jsonschema.Draft202012Validator(self.schema).iter_errors(evidence)], [])

    # --- part A stays part A --------------------------------------------------------------------

    def test_01_a_clean_run_passes_part_a_on_both_backends(self):
        evidence, _ = self._record_to()
        self._validate(evidence)
        for backend in ("claude", "codex"):
            with self.subTest(backend=backend):
                self.assertEqual(evidence["backends"][backend]["status"], "PASS", json.dumps(
                    [c for c in evidence["backends"][backend]["criteria"]
                     if c["result"] != "PASS"], indent=2))

    def test_02_the_gate_stays_partial_even_when_everything_passes(self):
        """Part A must never report more than part A, whatever its own results are."""
        evidence, _ = self._record_to()
        self.assertEqual(evidence["status"], "PARTIAL")
        self.assertTrue(all(b["status"] == "PASS" for b in evidence["backends"].values()))

    def test_03_criteria_five_to_eight_are_never_claimed(self):
        evidence, _ = self._record_to()
        ids = [c["id"] for c in evidence["criteria"]]
        for backend in ("claude", "codex"):
            ids += [c["id"] for c in evidence["backends"][backend]["criteria"]]
        for forbidden in (".c5", ".c6", ".c7", ".c8"):
            self.assertFalse([i for i in ids if forbidden in i], forbidden)
        self.assertIn("T073", evidence["notes"])

    def test_04_no_credential_material_appears_in_the_evidence(self):
        import re
        text = json.dumps(self._record_to()[0])
        for label, pattern in {
            "jwt": r"\beyJ[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}\.",
            "anthropic key": r"\bsk-ant-[a-z]{3}[0-9]{2}-",
            "openai key": r"\bsk-[A-Za-z0-9]{24,}",
            "bearer value": r"[Bb]earer\s+[A-Za-z0-9._\-]{12,}",
        }.items():
            with self.subTest(pattern=label):
                self.assertIsNone(re.search(pattern, text), label)

    # --- criterion 1 ------------------------------------------------------------------------------

    def test_05_a_count_that_is_not_n_fails(self):
        for actual in (2, 4):
            with self.subTest(counted=actual):
                self._write_captures("claude", tool_calls=actual)
                results, part_a = self._results()
                self.assertEqual(results["G11.c1.claude"], "FAIL")
                self.assertEqual(part_a, "FAIL")

    def test_06_a_ground_truth_that_disagrees_with_the_task_fails(self):
        """The parser counting 3 is worthless if the VM says 3 calls did not happen."""
        self._write("markers-claude.txt", "markers=2\n")
        results, _ = self._results()
        self.assertEqual(results["G11.c1.claude"], "FAIL")

    def test_07_a_broken_count_capture_fails_and_blocks_the_rest(self):
        (self.captures / "claude" / "count.jsonl").write_text("{not json\n", encoding="utf-8")
        results, _ = self._results()
        self.assertEqual(results["G11.c1.claude"], "FAIL")
        self.assertEqual(results["G11.c2.claude"], "NOT-RUN")

    # --- criterion 2 ------------------------------------------------------------------------------

    def test_08_an_imitation_counted_as_a_real_call_fails(self):
        self._write_captures("claude", spoof_counted=True)
        results, _ = self._results()
        self.assertEqual(results["G11.c2.claude"], "FAIL")

    def test_09_a_spoof_test_that_never_contained_the_imitation_fails(self):
        """A clean result against a capture with no imitation in it proves nothing."""
        self._write_captures("claude", spoof_present=False)
        results, _ = self._results()
        self.assertEqual(results["G11.c2.claude"], "FAIL")

    def test_10_the_imitation_is_really_present_in_the_passing_fixture(self):
        text = (self.captures / "claude" / "spoof.jsonl").read_text(encoding="utf-8")
        self.assertIn(self.rec.SPOOF_CALL_ID, text)
        self.assertEqual(self.rec.spoof_outer_events(text), 0)
        self.assertEqual(self.pp.parse(text).tool_calls, 1)

    # --- criterion 3 ------------------------------------------------------------------------------

    def test_11_the_derived_variants_classify_fail_closed(self):
        for backend in ("claude", "codex"):
            with self.subTest(backend=backend):
                out = self.captures / backend
                self.assertEqual(
                    self.pp.parse_file(out / "malformed.jsonl").classification, self.pp.MALFORMED)
                self.assertEqual(
                    self.pp.parse_file(out / "truncated.jsonl").classification, self.pp.TRUNCATED)

    def test_12_a_variant_that_parses_clean_fails_the_criterion(self):
        shutil.copy(self.captures / "claude" / "count.jsonl",
                    self.captures / "claude" / "malformed.jsonl")
        results, _ = self._results()
        self.assertEqual(results["G11.c3.claude"], "FAIL")

    def test_13_derive_is_deterministic(self):
        first = (self.captures / "claude" / "malformed.jsonl").read_bytes()
        self.rec.derive(str(self.captures / "claude"))
        self.assertEqual((self.captures / "claude" / "malformed.jsonl").read_bytes(), first)

    # --- criterion 4 ------------------------------------------------------------------------------

    def test_14_a_clean_exit_on_the_kill_run_fails(self):
        """If the agent exited 0 it was not abruptly terminated, whatever the stream looks like."""
        results, _ = self._results(self._capture(**{"kill_claude_agent_exit": "0"}))
        self.assertEqual(results["G11.c4.claude"], "FAIL")

    def test_15_a_kill_that_never_reached_the_stream_fails(self):
        results, _ = self._results(self._capture(**{"kill_claude_stream_started": "0"}))
        self.assertEqual(results["G11.c4.claude"], "FAIL")

    def test_16_an_undelivered_kill_signal_fails(self):
        results, _ = self._results(self._capture(**{"kill_claude_signal_exit": "1"}))
        self.assertEqual(results["G11.c4.claude"], "FAIL")

    def test_17_abnormal_termination_can_never_be_success(self):
        for status in ("137", "143", "1", "255"):
            with self.subTest(exit=status):
                outcome, _ = self.pp.run_outcome(
                    self.pp.parse_file(self.captures / "claude" / "count.jsonl"), status)
                self.assertEqual(outcome, self.pp.ABNORMAL)

    # --- bindings and independence ----------------------------------------------------------------

    def test_18_codex_without_strict_on_the_command_line_fails(self):
        for flag in ("", "--safety balanced", "strict"):
            with self.subTest(flag=flag):
                results, _ = self._results(
                    self._capture(**{"safety_flag_codex": flag}), backend="codex")
                self.assertEqual(results["G11.invocation.codex"], "FAIL")

    def test_19_a_base_mismatch_fails(self):
        results, _ = self._results(
            self._capture(**{"sbx_resolved_base_claude": "docker/sandbox-templates:wrong"}))
        self.assertEqual(results["G11.base"], "FAIL")

    def test_20_one_backend_failing_does_not_fail_the_other(self):
        self._write_captures("claude", tool_calls=1)
        evidence, _ = self._record_to()
        self.assertEqual(evidence["backends"]["claude"]["status"], "FAIL")
        self.assertEqual(evidence["backends"]["codex"]["status"], "PASS")
        self._validate(evidence)

    def test_21_an_unavailable_backend_is_not_run_with_a_reason(self):
        original = self.rec.availability
        self.rec.availability = lambda b, v: ((False, "G3 is 'FAIL', not PASS") if b == "codex"
                                              else original(b, v))
        self.addCleanup(setattr, self.rec, "availability", original)
        evidence, _ = self._record_to()
        self.assertEqual(evidence["backends"]["codex"]["status"], "NOT-RUN")
        self.assertTrue(evidence["backends"]["codex"]["not_run_reason"])
        self.assertEqual(evidence["backends"]["claude"]["status"], "PASS")
        self._validate(evidence)

    def test_22_a_tty_fails_the_invocation(self):
        self._write("vmstate-claude.txt",
                    f"stdin_is_tty=yes\nagent_binary={self.versions['docker_agent']}\n")
        results, _ = self._results()
        self.assertEqual(results["G11.invocation.claude"], "FAIL")

    def test_23_an_unpinned_in_vm_binary_fails(self):
        self._write("vmstate-codex.txt", "stdin_is_tty=no\nagent_binary=v1.999.0\n")
        results, _ = self._results(backend="codex")
        self.assertEqual(results["G11.invocation.codex"], "FAIL")

    def test_24_a_leftover_sandbox_fails_cleanup(self):
        self._write("ls-after.json", {"sandboxes": [{"name": "dca-g11-claude"}]})
        criteria, _, _, _, _ = self.rec.evaluate(
            self._capture(), str(self.work), self.versions)
        self.assertEqual({c["id"]: c["result"] for c in criteria}["G11.clean"], "FAIL")

    def test_25_a_changed_global_policy_fails_the_fingerprint(self):
        self._write("policy-after.json", {"rules": []})
        criteria, _, _, _, _ = self.rec.evaluate(
            self._capture(), str(self.work), self.versions)
        self.assertEqual({c["id"]: c["result"] for c in criteria}["G11.fingerprint"], "FAIL")


if __name__ == "__main__":
    unittest.main()
