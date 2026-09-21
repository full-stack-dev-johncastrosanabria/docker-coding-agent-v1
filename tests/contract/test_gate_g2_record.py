"""Contract tests for the G2 recorder (tasks.md T022, gates/G2/record.py).

They run the decision logic against synthetic captures in a temporary work directory: no sandbox, no
sbx command, no ChatGPT call, no change to the committed pins or to the accepted G3/G4 evidence.

G2 SELECTS the V1 credential mechanism, so the tests concentrate on the ways that selection could be
made dishonestly:

  * a FAIL must never be recorded as `proxy-managed`. That is the one error that would put an
    untested mechanism into production;
  * a FAIL must not erase what G3 already proved. Trusted Codex stays available on the token-file
    fallback, and untrusted stays BLOCKED;
  * the test is meaningless if `chatgpt-auth.json` is present, so its absence is proven by searching
    the filesystem, and a copy found anywhere fails the gate;
  * a clean scan is meaningless unless the scanner can find a planted canary first;
  * `auth.openai.com` is never promoted to an in-VM refresh host without runtime evidence;
  * a G3 that is not a current PASS makes G2 NOT-RUN, which is a different fact from a mechanism
    that was tested and rejected.
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
MARKER = "DCA-G2-OK"


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def run_capture(answer=MARKER, include_answer=True, provider="chatgpt", model="gpt-5.5"):
    events = [{"type": "team_info", "agent_name": "root",
               "available_agents": [{"name": "root", "provider": provider, "model": model}]},
              {"type": "user_message", "message": f"Reply with exactly the token {MARKER}"},
              {"type": "stream_started", "session_id": "s1"}]
    if include_answer:
        events += [{"type": "agent_choice", "content": chunk, "session_id": "s1"}
                   for chunk in (answer[i:i + 4] for i in range(0, len(answer), 4))]
    events += [{"type": "message_added", "session_id": "s1"},
               {"type": "stream_stopped", "reason": "normal"}]
    return "".join(json.dumps(e) + "\n" for e in events)


def scan_capture(patterns, locations=("home", "etc", "tmp", "run", "environment", "proc-environ"),
                 status="ok", hits=None):
    """A scan capture in gates/G2/scan.py's output shape: counts and paths, never values."""
    all_patterns = sorted(set(list(patterns) + [
        "chatgpt-auth-store", "chatgpt-account-id", "oauth-access-field", "oauth-refresh-field",
        "oauth-id-token-field", "oauth-access-camel", "oauth-refresh-camel", "jwt-material",
        "openai-project-key", "openai-api-key", "authorization-bearer",
        "chatgpt-session-cookie"]))
    out = []
    for location in locations:
        out.append(f"scan location={location} status={status}")
        for name in all_patterns:
            count = patterns.get(name, 0) if location == locations[0] else 0
            out.append(f"scan location={location} pattern={name} "
                       f"found={'yes' if count else 'no'} matches={count}")
            if count and hits:
                for path in hits.get(name, []):
                    out.append(f"hit location={location} pattern={name} path={path}")
    return "\n".join(out) + "\n"


CANARY_HITS = {"chatgpt-auth-store": 1, "oauth-access-field": 1, "oauth-refresh-field": 1,
               "jwt-material": 2}


class G2Recorder(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.record_module = _load("g2_record", GATES / "G2" / "record.py")
        cls.common = cls.record_module.common
        cls.preflight_module = cls.common.preflight_module
        cls.evidence_schema = json.loads(
            (GATES / "evidence.schema.json").read_text(encoding="utf-8"))
        cls.versions = json.loads((ROOT / "runtime" / "versions.yaml").read_text(encoding="utf-8"))
        cls.allow, cls.deny = cls.common.codex_policy("trusted")

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.work = self.tmp / "work"
        self.work.mkdir()
        self.g3 = self.tmp / "G3.json"
        self._write_g3()
        self._write_work()

    def _write(self, name, document):
        (self.work / name).write_text(
            document if isinstance(document, str) else json.dumps(document), encoding="utf-8")

    def _write_g3(self, status="PASS", model="gpt-5.5", versions=None):
        document = {
            "gate": "G3", "status": status,
            "run_at": "2026-09-21T02:17:28Z",
            "versions": {"sbx": self.versions["sbx"]["exact"]},
            "provenance": self.common.rules_module.evidence_provenance(
                "G3", versions or self.versions),
            "criteria": [{"id": "G3.model", "description": "d", "result": "PASS",
                          "evidence_ref": "e"}],
            "codex_backend": {"provider": "chatgpt", "selected_model": model,
                              "model_fallback_applied": True, "safety": "strict",
                              "token_file_fallback_proven": True,
                              "in_vm_refresh_required": False},
        }
        self.g3.write_text(json.dumps(document), encoding="utf-8")

    def _vmstate(self, **overrides):
        values = {
            "config_dir_entries": "0",
            "credential_files_found": "0",
            "credential_paths": "",
            "stdin_is_tty": "no",
            "agent_binary": self.versions["docker_agent"],
        }
        values.update({f"apikey_{n}": "absent" for n in self.common.API_KEY_NAMES})
        values.update(overrides)
        return "".join(f"{k}={v}\n" for k, v in values.items())

    def _write_work(self):
        pins = self.versions
        self._write("pf-version.json", {
            "client": {"version": pins["sbx"]["exact"]},
            "server": {"version": pins["sbx"]["exact"], "state": "running"}})
        self._write("pf-ssh-forwarding.json",
                    {"key": "ssh.agentForwardingEnabled", "value": False})
        self._write("pf-ssh-socket.json", {"key": "ssh.agentSocketPath", "value": ""})
        baseline = {"rules": [dict(self.preflight_module.BOOTSTRAP_RULE,
                                   name="default-deny-all", editable=False)]}
        accepted, why = self.common.accepted_network_state(versions=self.versions)
        self.assertIsNone(why, why)
        self.assertEqual(self.common.fingerprint(baseline, False), accepted)
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

        image, digest = self.common.codex_base(self.versions)
        repository, _, tag = image.rpartition(":")
        self._write("templates.json", {"images": [
            {"repository": f"docker.io/{repository}", "tag": tag,
             "id": digest.removeprefix("sha256:")[:12]}]})

        for slot in ("a", "b"):
            self._write(f"vmstate-{slot}.txt", self._vmstate())
            self._write(f"task-{slot}.json", run_capture())
            self._write(f"ls-before-{slot}.json", {"sandboxes": []})
            self._write(f"scan-canary-{slot}.txt", scan_capture(CANARY_HITS, locations=("tmp",)))
            self._write(f"scan-{slot}.txt", scan_capture({}))
        self._write("ls-between-a.json", {"sandboxes": []})
        self._write("secrets.txt",
                    "SCOPE      TYPE      NAME        SECRET\n"
                    "(global)   service   openai      (oauth configured)\n")

    def _capture(self, **overrides):
        obs = {
            "sbx_env_ssh_auth_sock": "removed",
            "pf_version_exit": "0", "pf_ssh_forwarding_exit": "0", "pf_ssh_socket_exit": "0",
            "pf_policy_exit": "0", "pf_ls_exit": "0",
            "governance_before_exit": "1", "governance_after_exit": "1",
            "ls_after_exit": "0", "policy_after_exit": "0", "templates_exit": "0",
            "sbx_resolved_base": self.common.codex_base(self.versions)[0],
            "policy_allow": ",".join(self.allow), "policy_deny": ",".join(self.deny),
            "policy_rules_exit": "0", "safety_flag": "strict", "secrets_exit": "0",
            "selected_model": "gpt-5.5",
        }
        for slot in ("a", "b"):
            obs.update({
                f"ls_before_{slot}_exit": "0", f"create_dca-g2-{slot}_exit": "0",
                f"policy_rules_{slot}_exit": "0", f"cp_agent_{slot}_exit": "0",
                f"cp_tasks_{slot}_exit": "0", f"cp_scan_{slot}_exit": "0",
                f"cp_shared_{slot}_exit": "0", f"vmstate_{slot}_exit": "0",
                f"task_{slot}_exit": "0", f"scan_canary_{slot}_exit": "0",
                f"scan_{slot}_exit": "0", f"rm_dca-g2-{slot}_exit": "0",
                f"ls_between_{slot}_exit": "0",
            })
        obs.update(overrides)
        return obs

    def _results(self, obs=None):
        criteria, _, _ = self.record_module.evaluate(
            obs or self._capture(), str(self.work), self.versions, g3_path=str(self.g3))
        return {row["id"]: row["result"] for row in criteria}

    def _record_to(self, obs=None):
        obs = obs or self._capture()
        obs_path = self.tmp / "observations.env"
        obs_path.write_text("".join(f"{k}={v}\n" for k, v in obs.items()), encoding="utf-8")
        evidence_path = self.tmp / "G2.json"
        original = self.record_module.G3_EVIDENCE
        self.record_module.G3_EVIDENCE = str(self.g3)
        self.addCleanup(setattr, self.record_module, "G3_EVIDENCE", original)
        status, _ = self.record_module.record(
            str(obs_path), str(self.work), str(ROOT / "runtime" / "versions.yaml"),
            str(evidence_path))
        return status, json.loads(evidence_path.read_text(encoding="utf-8"))

    def _validate(self, evidence):
        self.assertEqual(
            [e.message for e in
             jsonschema.Draft202012Validator(self.evidence_schema).iter_errors(evidence)], [])

    # --- the clean, PASS path -------------------------------------------------------------------------

    def test_01_a_clean_capture_selects_proxy_managed(self):
        status, evidence = self._record_to()
        self.assertEqual(status, "PASS", json.dumps(
            [c for c in evidence["criteria"] if c["result"] != "PASS"], indent=2))
        self._validate(evidence)
        backend = evidence["codex_backend"]
        self.assertEqual(backend["credential_mechanism"], "proxy-managed")
        self.assertIs(backend["proxy_managed_token_readable"], False)
        self.assertEqual(backend["selected_model"], "gpt-5.5")
        self.assertIn("G9", backend["untrusted_implication"])
        self.assertIsNone(evidence["fallback_applied"])

    def test_02_no_secret_material_appears_in_the_evidence(self):
        import re
        text = json.dumps(self._record_to()[1])
        for label, pattern in {
            "jwt": r"\beyJ[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}\.",
            "bearer value": r"[Bb]earer\s+[A-Za-z0-9._\-]{12,}",
            "oauth json value": r'"(access_token|refresh_token|id_token)"\s*:\s*"[^"]{8,}"',
            "api key": r"\bsk-[A-Za-z0-9\-]{16,}",
            "long opaque blob": r'"[A-Za-z0-9+/]{80,}={0,2}"',
        }.items():
            with self.subTest(pattern=label):
                self.assertIsNone(re.search(pattern, text), label)

    # --- the G3 dependency ----------------------------------------------------------------------------

    def test_03_a_g3_that_is_not_pass_makes_g2_not_run(self):
        for status in ("FAIL", "NOT-RUN", "PARTIAL"):
            with self.subTest(g3=status):
                self._write_g3(status=status)
                recorded, evidence = self._record_to()
                self.assertEqual(recorded, "NOT-RUN")
                self.assertIsNotNone(evidence["not_run_reason"])
                self.assertIsNone(evidence["codex_backend"]["credential_mechanism"])
                self._validate(evidence)

    def test_04_a_stale_g3_is_never_interpreted_as_permission_to_proceed(self):
        stale = dict(self.versions)
        stale["docker_agent"] = "v1.999.0"
        self._write_g3(versions=stale)
        status, evidence = self._record_to()
        self.assertEqual(status, "NOT-RUN")
        self.assertIn("stale", evidence["not_run_reason"])

    def test_05_a_g3_without_a_verified_model_does_not_run(self):
        self._write_g3(model="")
        self.assertEqual(self._record_to()[0], "NOT-RUN")

    # --- the test is meaningless if the credential file is present -------------------------------------

    def test_06_a_credential_file_anywhere_in_the_vm_fails(self):
        self._write("vmstate-b.txt", self._vmstate(
            credential_files_found="1", credential_paths="/home/agent/.config/cagent/chatgpt-auth.json"))
        results = self._results()
        self.assertEqual(results["G2.no-credential-file"], "FAIL")
        self.assertEqual(results["G2.proxy-execution"], "NOT-RUN")

    def test_07_a_non_empty_config_directory_fails(self):
        self._write("vmstate-a.txt", self._vmstate(config_dir_entries="1"))
        self.assertEqual(self._results()["G2.no-credential-file"], "FAIL")

    # --- proxy-managed execution ------------------------------------------------------------------------

    def test_08_the_second_fresh_sandbox_failing_fails_the_gate(self):
        status, evidence = self._record_to(self._capture(task_b_exit="1"))
        self.assertEqual(status, "FAIL")
        self.assertEqual(evidence["codex_backend"]["credential_mechanism"],
                         "token-file-trusted-only")

    def test_09_a_run_with_no_marker_answer_fails(self):
        self._write("task-a.json", run_capture(include_answer=False))
        self.assertEqual(self._results()["G2.proxy-execution"], "FAIL")

    def test_10_a_run_on_a_model_g3_did_not_verify_fails(self):
        self._write("task-b.json", run_capture(model="gpt-5.6"))
        self.assertEqual(self._results()["G2.proxy-execution"], "FAIL")

    def test_11_two_sandboxes_live_at_once_fails(self):
        self._write("ls-before-b.json", {"sandboxes": [{"name": "dca-g2-a"}]})
        self.assertEqual(self._results()["G2.proxy-execution"], "FAIL")

    def test_12_a_tty_fails(self):
        self._write("vmstate-a.txt", self._vmstate(stdin_is_tty="yes"))
        self.assertEqual(self._results()["G2.proxy-execution"], "FAIL")

    # --- the canary control -----------------------------------------------------------------------------

    def test_13_a_scanner_that_cannot_find_the_canary_fails(self):
        """Without this control a scanner that matches nothing looks like perfect isolation."""
        self._write("scan-canary-b.txt", scan_capture({}, locations=("tmp",)))
        results = self._results()
        self.assertEqual(results["G2.canary"], "FAIL")
        self.assertEqual(results["G2.token-material"], "NOT-RUN")

    def test_14_a_canary_scan_that_errored_fails(self):
        self._write("scan-canary-a.txt",
                    scan_capture(CANARY_HITS, locations=("tmp",), status="error"))
        self.assertEqual(self._results()["G2.canary"], "FAIL")

    def test_15_an_unreadable_canary_scan_fails(self):
        self._write("scan-canary-a.txt", "")
        self.assertEqual(self._results()["G2.canary"], "FAIL")

    # --- readable token material --------------------------------------------------------------------------

    def test_16_readable_token_material_fails_and_selects_the_fallback(self):
        for pattern in ("chatgpt-auth-store", "oauth-access-field", "jwt-material",
                        "openai-api-key", "chatgpt-session-cookie"):
            with self.subTest(pattern=pattern):
                self._write("scan-a.txt", scan_capture(
                    {pattern: 1}, hits={pattern: ["/home/agent/.config/cagent/chatgpt-auth.json"]}))
                results = self._results()
                self.assertEqual(results["G2.token-material"], "FAIL")
                status, evidence = self._record_to()
                self.assertEqual(status, "FAIL")
                self.assertEqual(evidence["codex_backend"]["credential_mechanism"],
                                 "token-file-trusted-only")
                self.assertIs(evidence["codex_backend"]["proxy_managed_token_readable"], True)
                self.setUp()

    def test_17_a_bearer_reference_alone_does_not_fail_the_gate(self):
        """A shell or template reference is plumbing, not a disclosure - G1b's lesson."""
        self._write("scan-a.txt", scan_capture({"authorization-bearer": 1}))
        self.assertEqual(self._results()["G2.token-material"], "PASS")

    def test_18_a_scan_that_errored_is_never_a_clean_result(self):
        self._write("scan-b.txt", scan_capture({}, status="error"))
        self.assertEqual(self._results()["G2.token-material"], "FAIL")

    def test_19_an_unreadable_scan_is_never_a_clean_result(self):
        (self.work / "scan-b.txt").unlink()
        self.assertEqual(self._results()["G2.token-material"], "FAIL")

    # --- a FAIL must not be dressed up, and must not erase G3 ---------------------------------------------

    def test_20_a_failure_is_never_recorded_as_proxy_managed(self):
        """The one error that would put an untested mechanism into production."""
        for change in ({"task_a_exit": "1"}, {"scan_a_exit": "1"}, {"rm_dca-g2-b_exit": "1"}):
            with self.subTest(change=change):
                status, evidence = self._record_to(self._capture(**change))
                self.assertEqual(status, "FAIL")
                self.assertNotEqual(evidence["codex_backend"]["credential_mechanism"],
                                    "proxy-managed")
                self.assertEqual(evidence["codex_backend"]["credential_mechanism"],
                                 "token-file-trusted-only")
                self.setUp()

    def test_21_a_failure_preserves_g3s_trusted_fallback(self):
        _, evidence = self._record_to(self._capture(task_b_exit="1"))
        backend = evidence["codex_backend"]
        self.assertTrue(backend["token_file_fallback_proven"])
        self.assertIn("trusted Codex continues", backend["trusted_implication"])
        self.assertIn("chatgpt-auth.json", backend["trusted_implication"])

    def test_22_untrusted_is_blocked_under_the_token_file_fallback(self):
        _, evidence = self._record_to(self._capture(task_b_exit="1"))
        untrusted = evidence["codex_backend"]["untrusted_implication"]
        self.assertIn("BLOCKED", untrusted)
        self.assertIn("harness: codex", untrusted)
        self.assertIn("API key", untrusted)

    def test_23_a_pass_does_not_by_itself_make_codex_untrusted_eligible(self):
        _, evidence = self._record_to()
        self.assertIn("G9", evidence["codex_backend"]["untrusted_implication"])
        self.assertIn("NOT", evidence["codex_backend"]["untrusted_implication"])

    def test_24_auth_openai_com_is_not_promoted_by_this_gate(self):
        """A promotion needs runtime refresh evidence and its own T014/T015 path."""
        state = self.common.refresh_candidate_state()
        self.assertEqual(state["host"], "auth.openai.com")
        self.assertEqual(state["purpose"], "host-oauth-login")
        self.assertIs(state["sandbox_required"], False)
        self.assertEqual(state["profiles"], [])
        _, evidence = self._record_to()
        self.assertNotIn("auth.openai.com", json.dumps(evidence["criteria"]))

    # --- bindings and cleanup -----------------------------------------------------------------------------

    def test_25_a_base_mismatch_fails(self):
        self.assertEqual(self._results(self._capture(
            sbx_resolved_base="docker/sandbox-templates:claude-code-docker"))["G2.base"], "FAIL")

    def test_26_a_policy_that_is_not_the_accepted_one_fails(self):
        self.assertEqual(
            self._results(self._capture(policy_allow="chatgpt.com"))["G2.policy"], "FAIL")

    def test_27_a_safety_flag_other_than_strict_fails(self):
        for flag in ("balanced", "restricted", "autonomous", ""):
            with self.subTest(flag=flag):
                self.assertEqual(self._results(self._capture(safety_flag=flag))["G2.strict"],
                                 "FAIL")

    def test_28_an_unpinned_in_vm_binary_fails(self):
        self._write("vmstate-a.txt", self._vmstate(agent_binary="v1.999.0"))
        self.assertEqual(self._results()["G2.pinned-binary"], "FAIL")

    def test_29_a_cleanup_failure_fails(self):
        self._write("ls-after.json", {"sandboxes": [{"name": "dca-g2-a"}]})
        self.assertEqual(self._results()["G2.clean"], "FAIL")

    def test_30_a_changed_global_policy_fails_the_fingerprint(self):
        self._write("policy-after.json", {"rules": []})
        self.assertEqual(self._results()["G2.fingerprint"], "FAIL")

    # --- the gate's own fixtures must never fire the decision patterns ---------------------------------

    def test_31_nothing_copied_into_the_sandbox_fires_the_decision_patterns(self):
        """A token-shaped fixture shipped into the VM manufactures a FAIL the proxy never caused.

        The canary was originally copied in as part of the tasks directory while only
        /tmp/dca-g2-canary.json was deleted before the real scan, so the decision scan found the
        gate's OWN file and fired four token-material patterns - a guaranteed FAIL that says nothing
        about proxy-managed OAuth. The canary is now written inside the VM and removed before the
        real scan, so nothing this gate copies in may match.
        """
        scan = _load("g2_scan", GATES / "G2" / "scan.py")
        run_sh = (GATES / "G2" / "run.sh").read_text(encoding="utf-8")
        self.assertNotIn("tasks/canary.json", run_sh)
        for path in (GATES / "G2" / "agent.yaml", GATES / "G2" / "scan.py",
                     GATES / "G1b" / "scan.py"):
            with self.subTest(copied=path.name):
                blob = path.read_bytes()
                fired = [n for n in scan.TOKEN_MATERIAL if scan.PATTERNS[n].search(blob)]
                self.assertEqual(fired, [], f"{path.name} fires {fired} in the decision scan")

    def test_32_a_privileged_scan_that_did_not_complete_is_never_clean(self):
        """An unfinished scan reporting no token material is the false negative this gate prevents."""
        for change in ({"scan_a_exit": "1"}, {"scan_b_exit": "3"}):
            with self.subTest(change=change):
                self.assertEqual(
                    self._results(self._capture(**change))["G2.token-material"], "FAIL")

    def test_33_a_canary_control_that_did_not_complete_fails(self):
        self.assertEqual(self._results(self._capture(scan_canary_b_exit="1"))["G2.canary"], "FAIL")

    def test_34_a_scan_that_did_not_really_happen_is_recorded_as_unknown(self):
        """An unobserved negative reported as an observed one is as dishonest as a false PASS.

        proxy_managed_token_readable=false must mean "both sandboxes were searched and nothing was
        found", never "no finding reached this field".
        """
        cases = {
            "the scan capture is missing": lambda: [
                (self.work / f"scan-{s}.txt").unlink() for s in ("a", "b")],
            "the scan command failed": lambda: None,
            "the canary never validated the scanner": lambda: [
                self._write(f"scan-canary-{s}.txt", scan_capture({}, locations=("tmp",)))
                for s in ("a", "b")],
        }
        for label, mutate in cases.items():
            with self.subTest(case=label):
                obs = self._capture(scan_a_exit="1") if "command failed" in label \
                    else self._capture()
                mutate()
                _, evidence = self._record_to(obs)
                self.assertIsNone(evidence["codex_backend"]["proxy_managed_token_readable"])
                self._validate(evidence)
                self.setUp()

    def test_35_a_completed_validated_scan_reports_its_finding(self):
        """A real observation is not discarded just because an earlier criterion failed closed.

        The live gate failed at execution while the privileged scan still ran, searched all six
        locations in both sandboxes and found nothing. That IS evidence and must be recorded.
        """
        _, clean = self._record_to()
        self.assertIs(clean["codex_backend"]["proxy_managed_token_readable"], False)

        _, failed_execution = self._record_to(self._capture(task_a_exit="1", task_b_exit="1"))
        results = {c["id"]: c["result"] for c in failed_execution["criteria"]}
        self.assertEqual(results["G2.token-material"], "NOT-RUN")
        self.assertIs(failed_execution["codex_backend"]["proxy_managed_token_readable"], False)
        self.assertEqual(failed_execution["codex_backend"]["credential_mechanism"],
                         "token-file-trusted-only")


if __name__ == "__main__":
    unittest.main()
