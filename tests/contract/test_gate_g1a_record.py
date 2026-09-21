"""Contract tests for the G1a recorder (tasks.md T016, gates/G1a/record.py).

They run the decision logic against synthetic captures in a temporary work directory: no sandbox,
no sbx command, no Claude call, no change to the committed pins or to the accepted G4 evidence.
The shapes mirror what the pinned sbx v0.43.0 and Docker Agent v1.136.0 actually emitted.

Two of these tests exist because the first implementation got the same question wrong in both
directions, and both errors are invisible to a reader of the capture:

  * the Claude harness runs with --include-partial-messages, so one answer arrives as a SEQUENCE
    of agent_choice deltas ("DCA-G1A-", "OK"). Joining them with a newline makes a CORRECT answer
    unrecognizable, which fails a working backend;
  * the user_message event echoes the prompt back verbatim, and the prompt contains the marker.
    Reading the answer from any text in the document makes a run in which the model never
    answered look successful, which passes a broken one.

The third property under test is the criterion correction T016 records: the plan is proven by step
1's interactive login, because a sandbox that inherits the host-side credential reports
subscriptionType null - and that null must be recorded verbatim, never rewritten as Pro.
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
LOGIN = "dca-g1a-login"
FRESH = "dca-g1a-fresh"
MARKER = "DCA-G1A-OK"


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def auth_doc(logged_in=True, method="claude.ai", provider="firstParty", plan="pro"):
    return {"loggedIn": logged_in, "authMethod": method,
            "apiProvider": provider, "subscriptionType": plan}


def run_capture(answer_deltas=("DCA-G1A-", "OK"), prompt=None, include_answer=True):
    """A `docker agent run --exec --json` capture in the pinned JSON-Lines shape."""
    prompt = prompt if prompt is not None else f"Reply with exactly the token {MARKER}"
    events = [
        {"type": "team_info", "agent_name": "root"},
        {"type": "toolset_info", "available_tools": 0},
        {"type": "user_message", "message": prompt},
        {"type": "stream_started", "session_id": "s1"},
        {"type": "agent_info", "agent_name": "root", "model": "claude-code"},
    ]
    if include_answer:
        events += [{"type": "agent_choice", "content": d, "session_id": "s1"}
                   for d in answer_deltas]
    events += [
        {"type": "message_added", "session_id": "s1"},
        {"type": "token_usage", "usage": {"input_tokens": 1, "output_tokens": 2}},
        {"type": "stream_stopped", "reason": "normal"},
    ]
    return "".join(json.dumps(e) + "\n" for e in events)


class G1aRecorder(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.record_module = _load("g1a_record", GATES / "G1a" / "record.py")
        cls.common = cls.record_module.common
        cls.preflight_module = cls.common.preflight_module
        cls.rules = cls.common.rules_module
        cls.evidence_schema = json.loads(
            (GATES / "evidence.schema.json").read_text(encoding="utf-8"))
        cls.versions = json.loads((ROOT / "runtime" / "versions.yaml").read_text(encoding="utf-8"))
        cls.allow, cls.deny = cls.common.claude_policy("trusted")
        cls.login_hosts = cls.common.claude_login_hosts()

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.work = self.tmp / "work"
        self.work.mkdir()
        self._write_work()

    # --- a clean synthetic run ------------------------------------------------------------------

    def _write(self, name, document):
        (self.work / name).write_text(
            document if isinstance(document, str) else json.dumps(document), encoding="utf-8")

    def _write_work(self):
        pins = self.versions
        self._write("pf-version.json", {
            "client": {"version": pins["sbx"]["exact"]},
            "server": {"version": pins["sbx"]["exact"], "state": "running"}})
        self._write("pf-ssh-forwarding.json",
                    {"key": "ssh.agentForwardingEnabled", "value": False})
        self._write("pf-ssh-socket.json", {"key": "ssh.agentSocketPath", "value": ""})
        # The fingerprint covers the COMPLETE rule shape, so the synthetic baseline has to carry
        # the fields the real document carries. preflight.BOOTSTRAP_RULE is the identity check's
        # subset and omits name/editable; hashing it would produce a value that is not the one
        # accepted G4 recorded, and G1a.0e would fail for a reason that has nothing to do with
        # the property under test. The assertion below keeps the two in step.
        baseline = {"rules": [dict(self.preflight_module.BOOTSTRAP_RULE,
                                   name="default-deny-all", editable=False)]}
        accepted, why = self.common.accepted_network_state(versions=self.versions)
        self.assertIsNone(why, why)
        self.assertEqual(
            self.common.fingerprint(baseline, False), accepted,
            "the synthetic global baseline must hash to the fingerprint accepted G4 recorded")
        self._write("pf-policy.json", baseline)
        self._write("policy-after.json", baseline)
        self._write("pf-ls.json", {"sandboxes": []})
        self._write("ls-after.json", {"sandboxes": []})
        governance = {"allowed": False, "deny_kind": "implicit",
                      "resource_value": self.common.GOVERNANCE_PROBE,
                      "target": self.common.GOVERNANCE_PROBE,
                      "governance": {"active": False}}
        self._write("governance-before.json", governance)
        self._write("governance-after.json", governance)

        image, digest = self.common.claude_base(self.versions)
        repository, _, tag = image.rpartition(":")
        self._write("templates.json", {"images": [
            {"repository": f"docker.io/{repository}", "tag": tag,
             "id": digest.removeprefix("sha256:")[:12]}]})

        # Step 1 proves the plan; the fresh sandbox reports null on the inherited path.
        self._write(f"auth-{LOGIN}.json", auth_doc(plan="pro"))
        self._write(f"auth-{FRESH}-pre.json", auth_doc(plan=None))
        self._write(f"auth-{FRESH}-post.json", auth_doc(plan=None))
        self._write(f"task-{LOGIN}.json", run_capture())
        self._write(f"task-{FRESH}.json", run_capture())
        self._write(f"apikeys-{FRESH}.txt",
                    "".join(f"{n}=absent\n" for n in self.record_module.API_KEY_NAMES))
        self._write(f"versions-{FRESH}.txt",
                    f"claude_code=2.1.246\ndocker_agent={self.versions['docker_agent']}\n")
        for host in self.login_hosts:
            self._write(f"check-{FRESH}-{host}.json",
                        {"allowed": False, "deny_kind": "explicit",
                         "reason": "Denied by local rule"})

    def _capture(self, **overrides):
        obs = {
            "sbx_env_ssh_auth_sock": "removed",
            "pf_version_exit": "0", "pf_ssh_forwarding_exit": "0", "pf_ssh_socket_exit": "0",
            "pf_policy_exit": "0", "pf_ls_exit": "0",
            "governance_before_exit": "1", "governance_after_exit": "1",
            "ls_after_exit": "0", "policy_after_exit": "0", "templates_exit": "0",
            "sbx_resolved_base": self.common.claude_base(self.versions)[0],
            "policy_allow": ",".join(self.allow), "policy_deny": ",".join(self.deny),
            "policy_login_hosts": ",".join(self.login_hosts),
            "policy_rules_exit": "0", "policy_rules_fresh_exit": "0",
            f"create_{LOGIN}_exit": "0", f"auth_{LOGIN}_exit": "0", f"task_{LOGIN}_exit": "0",
            f"rm_{LOGIN}_exit": "0",
            f"create_{FRESH}_exit": "0", f"auth_{FRESH}-pre_exit": "0",
            f"auth_{FRESH}-post_exit": "0", f"task_{FRESH}_exit": "0",
            f"apikeys_{FRESH}_exit": "0", f"versions_{FRESH}_exit": "0", f"rm_{FRESH}_exit": "0",
        }
        obs.update(overrides)
        return obs

    def _results(self, obs):
        criteria, _, _, _ = self.record_module.evaluate(obs, str(self.work), self.versions)
        return {row["id"]: row["result"] for row in criteria}

    def _record_to(self, obs, name="G1a.json"):
        obs_path = self.tmp / "observations.env"
        obs_path.write_text("".join(f"{k}={v}\n" for k, v in obs.items()), encoding="utf-8")
        evidence_path = self.tmp / name
        status, _ = self.record_module.record(
            str(obs_path), str(self.work), str(ROOT / "runtime" / "versions.yaml"),
            str(evidence_path))
        return status, json.loads(evidence_path.read_text(encoding="utf-8"))

    # --- the streamed answer ---------------------------------------------------------------------

    def test_01_a_streamed_answer_is_concatenated_without_a_separator(self):
        """The regression this fixes: joining deltas with a newline failed a working backend."""
        answer = self.record_module.task_answer(run_capture(("DCA-G1A-", "OK")))
        self.assertEqual(answer, MARKER)
        for deltas in (("DCA-", "G1A", "-OK"), ("D", "C", "A", "-G1A-OK"), (MARKER,)):
            with self.subTest(deltas=deltas):
                self.assertEqual(self.record_module.task_answer(run_capture(deltas)), MARKER)

    def test_02_the_echoed_prompt_is_never_read_as_the_answer(self):
        """The prompt contains the marker, so a run with no answer must not look successful."""
        capture = run_capture(include_answer=False)
        self.assertIn(MARKER, capture)
        self.assertIsNone(self.record_module.task_answer(capture))

    def test_03_an_unreadable_capture_has_no_answer(self):
        for raw in ("", "   ", "not json", "{}\n"):
            with self.subTest(raw=raw):
                self.assertIsNone(self.record_module.task_answer(raw))

    def test_04_a_run_that_produced_no_answer_fails_the_step(self):
        self._write(f"task-{FRESH}.json", run_capture(include_answer=False))
        self.assertEqual(self._results(self._capture())["G1a.step2"], "FAIL")

    # --- the plan, split across the two steps ------------------------------------------------------

    def test_05_step1_must_explicitly_prove_the_pro_plan(self):
        """An inherited credential reports null; only a real interactive login reports the plan."""
        self._write(f"auth-{LOGIN}.json", auth_doc(plan=None))
        results = self._results(self._capture())
        self.assertEqual(results["G1a.step1"], "FAIL")

    def test_06_step2_may_report_a_null_plan_but_never_a_different_one(self):
        clean = self._results(self._capture())
        self.assertEqual(clean["G1a.step2.plan"], "PASS")
        for plan in ("pro", None):
            with self.subTest(plan=plan):
                self._write(f"auth-{FRESH}-post.json", auth_doc(plan=plan))
                self.assertEqual(self._results(self._capture())["G1a.step2.plan"], "PASS")
        for plan in ("free", "max", "team"):
            with self.subTest(plan=plan):
                self._write(f"auth-{FRESH}-post.json", auth_doc(plan=plan))
                self.assertEqual(self._results(self._capture())["G1a.step2.plan"], "FAIL")

    def test_07_step2_must_be_a_first_party_subscription_session(self):
        for field, value in (("method", "apiKey"), ("provider", "bedrock"),
                             ("logged_in", False)):
            with self.subTest(field=field):
                kwargs = {"plan": None}
                if field == "method":
                    kwargs["method"] = value
                elif field == "provider":
                    kwargs["provider"] = value
                else:
                    kwargs["logged_in"] = value
                self._write(f"auth-{FRESH}-pre.json", auth_doc(**kwargs))
                self.assertEqual(self._results(self._capture())["G1a.step2.auth"], "FAIL")

    def test_08_proves_pro_accepts_only_an_explicit_pro_plan(self):
        for plan, expected in (("pro", 0), ("PRO", 0), (None, 1), ("free", 1)):
            with self.subTest(plan=plan):
                path = self.tmp / "auth.json"
                path.write_text(json.dumps(auth_doc(plan=plan)), encoding="utf-8")
                self.assertEqual(
                    self.record_module.main(["record.py", "--proves-pro", str(path)]), expected)
        path = self.tmp / "auth.json"
        path.write_text(json.dumps(auth_doc(logged_in=False, plan="pro")), encoding="utf-8")
        self.assertEqual(self.record_module.main(["record.py", "--proves-pro", str(path)]), 1)

    # --- the security properties step 2 must hold --------------------------------------------------

    def test_09_a_provider_api_key_in_the_fresh_sandbox_fails(self):
        names = self.record_module.API_KEY_NAMES
        self._write(f"apikeys-{FRESH}.txt",
                    f"{names[0]}=present\n" + "".join(f"{n}=absent\n" for n in names[1:]))
        self.assertEqual(self._results(self._capture())["G1a.step2.nokey"], "FAIL")

    def test_10_an_unreadable_api_key_probe_cannot_pass(self):
        self._write(f"apikeys-{FRESH}.txt", "ANTHROPIC_API_KEY=maybe\n")
        self.assertEqual(self._results(self._capture())["G1a.step2.nokey"], "FAIL")

    def test_11_a_login_host_reachable_in_the_fresh_sandbox_fails(self):
        self._write(f"check-{FRESH}-{self.login_hosts[0]}.json", {"allowed": True})
        self.assertEqual(self._results(self._capture())["G1a.login-containment"], "FAIL")

    def test_12_an_unreadable_containment_check_cannot_pass(self):
        (self.work / f"check-{FRESH}-{self.login_hosts[0]}.json").unlink()
        self.assertEqual(self._results(self._capture())["G1a.login-containment"], "FAIL")

    # --- fail-closed -------------------------------------------------------------------------------

    def test_13_a_failed_preflight_leaves_the_steps_not_run(self):
        self._write("pf-ls.json", {"sandboxes": [{"name": "leftover"}]})
        results = self._results(self._capture())
        self.assertEqual(results["G1a.0d"], "FAIL")
        for row in ("G1a.step1", "G1a.step2", "G1a.step2.auth", "G1a.step2.nokey"):
            self.assertEqual(results[row], "NOT-RUN", row)

    def test_14_a_drifted_global_policy_stops_the_gate(self):
        """G1a must not prove anything under a policy G4 never verified."""
        widened = {"rules": [dict(self.preflight_module.BOOTSTRAP_RULE),
                             {"id": "x", "name": "x", "scope": "global", "applies_to": "all",
                              "resource_type": "network", "decision": "allow",
                              "resources": ["evil.example.com:443"], "origin": "local",
                              "layer": "local", "status": "active", "editable": True}]}
        self._write("pf-policy.json", widened)
        results = self._results(self._capture())
        self.assertEqual(results["G1a.0e"], "FAIL")
        self.assertEqual(results["G1a.step2"], "NOT-RUN")

    def test_15_a_substituted_base_fails(self):
        self.assertEqual(
            self._results(self._capture(sbx_resolved_base="docker/other:latest"))["G1a.base"],
            "FAIL")

    def test_16_a_policy_that_is_not_the_accepted_g4_one_fails(self):
        shortened = ",".join(self.allow[:-1])
        self.assertEqual(self._results(self._capture(policy_allow=shortened))["G1a.policy"],
                         "FAIL")

    def test_17_a_leftover_sandbox_or_moved_fingerprint_fails(self):
        self._write("ls-after.json", {"sandboxes": [{"name": FRESH}]})
        self.assertEqual(self._results(self._capture())["G1a.clean"], "FAIL")

    def test_18_a_wrong_docker_agent_artifact_fails(self):
        self._write(f"versions-{FRESH}.txt", "claude_code=2.1.246\ndocker_agent=v0.0.1\n")
        self.assertEqual(self._results(self._capture())["G1a.versions"], "FAIL")

    def test_19_the_in_sandbox_claude_build_is_recorded_not_asserted(self):
        """The host CLI pin and the build inside the pinned base are different facts (E14b)."""
        results = self._results(self._capture())
        self.assertEqual(results["G1a.versions"], "PASS")
        criteria, _, _, _ = self.record_module.evaluate(
            self._capture(), str(self.work), self.versions)
        row = next(c for c in criteria if c["id"] == "G1a.versions")
        self.assertIn("2.1.246", row["evidence_ref"])
        self.assertIn(self.versions["claude_code"]["exact"], row["evidence_ref"])

    # --- the evidence document ---------------------------------------------------------------------

    def test_20_a_clean_capture_passes_every_criterion(self):
        results = self._results(self._capture())
        self.assertTrue(results)
        self.assertEqual(set(results.values()), {"PASS"}, results)

    def test_21_a_pass_records_the_split_subscription_proof_as_a_typed_field(self):
        status, evidence = self._record_to(self._capture())
        self.assertEqual(status, "PASS")
        jsonschema.Draft202012Validator(self.evidence_schema).validate(evidence)
        self.assertEqual(evidence["gate"], "G1a")
        self.assertEqual(self.rules.evidence_problems(evidence, self.versions), [])
        proven = evidence["claude_subscription"]
        # The two observations stay separate: the fresh sandbox's null is never rewritten as Pro.
        self.assertEqual(proven["subscription_type_proven_at_login"], "pro")
        self.assertIsNone(proven["subscription_type_reported_by_fresh_sandbox"])
        self.assertTrue(proven["proven_without_login"])
        self.assertTrue(proven["proven_without_api_key"])
        self.assertEqual(proven["auth_method"], "claude.ai")
        self.assertEqual(proven["api_provider"], "firstParty")

    def test_22_a_fail_carries_no_subscription_claim(self):
        self._write(f"task-{FRESH}.json", run_capture(include_answer=False))
        status, evidence = self._record_to(self._capture())
        self.assertEqual(status, "FAIL")
        self.assertNotIn("claude_subscription", evidence)
        jsonschema.Draft202012Validator(self.evidence_schema).validate(evidence)

    def test_23_only_a_passing_g1a_may_carry_a_subscription_claim(self):
        _, passing = self._record_to(self._capture())
        validator = jsonschema.Draft202012Validator(self.evidence_schema)
        with self.subTest(case="another gate may not carry one"):
            with self.assertRaises(jsonschema.ValidationError):
                validator.validate(dict(passing, gate="G1c"))
        with self.subTest(case="a G1a PASS must carry one"):
            stripped = dict(passing)
            del stripped["claude_subscription"]
            with self.assertRaises(jsonschema.ValidationError):
                validator.validate(stripped)
        with self.subTest(case="the fresh sandbox's plan may be null or a string"):
            for value in (None, "pro"):
                claim = dict(passing["claude_subscription"],
                             subscription_type_reported_by_fresh_sandbox=value)
                validator.validate(dict(passing, claude_subscription=claim))

    def test_24_the_evidence_records_the_criterion_correction(self):
        _, evidence = self._record_to(self._capture())
        notes = evidence["notes"]
        self.assertIn("CRITERION CORRECTION", notes)
        self.assertIn("subscriptionType", notes)
        self.assertIn("null", notes)


if __name__ == "__main__":
    unittest.main()
