"""Contract tests for the G3 recorder (tasks.md T019, gates/G3/record.py).

They run the decision logic against synthetic captures in a temporary work directory: no sandbox, no
sbx command, no ChatGPT call, no change to the committed pins or to the accepted G4 evidence.

The properties under test are the ones a Codex gate can most plausibly get wrong in the FAIL-OPEN
direction:

  * the approval pipeline is decided by what happened to the workspace FILE, never by what the run
    reported. A blocked case whose file exists must fail, because that is the pipeline failing open;
  * the absence of a hook decision is not an allow;
  * a user config asking for `safety: autonomous` + `yolo: true` must not defeat `--safety strict`,
    and the check is behavioral: under autonomous the hook is never consulted, so a hook that still
    blocks is the proof;
  * "minimal credential" means exactly one file, checked on both sides, because copying the whole
    ~/.config/cagent would bring .env and cached state along with the token;
  * T019's model fallback accepts only a NON-DEPRECATED GPT-5.x. An older family must never be
    selected to make the gate pass, and no second provider is ever introduced;
  * two CONSECUTIVE fresh sandboxes means one live at a time - a second created while the first
    still exists proves nothing about freshness.
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
MARKER = "DCA-G3-OK"
WRITE_MARKER = "DCA-G3-WRITE"


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def models_table(rows=(("chatgpt", "gpt-5.6", True),)):
    out = "PROVIDER   MODEL     DEFAULT\n"
    for provider, model, default in rows:
        out += f"{provider}    {model}   {'*' if default else ''}\n"
    return out


def run_capture(answer=MARKER, include_answer=True, provider="chatgpt", model="gpt-5.5"):
    """A `docker agent run --exec --json` capture in the pinned JSON-Lines shape.

    team_info reports the EFFECTIVE provider and model, which is how the gate checks that the run
    used the verified selection rather than whatever a config happened to declare.
    """
    events = [{"type": "team_info", "agent_name": "root",
               "available_agents": [{"name": "root", "provider": provider, "model": model}]},
              {"type": "user_message", "message": f"Reply with exactly the token {MARKER}"},
              {"type": "stream_started", "session_id": "s1"}]
    if include_answer:
        events += [{"type": "agent_choice", "content": chunk, "session_id": "s1"}
                   for chunk in (answer[i:i + 5] for i in range(0, len(answer), 5))]
    events += [{"type": "message_added", "session_id": "s1"},
               {"type": "stream_stopped", "reason": "normal"}]
    return "".join(json.dumps(e) + "\n" for e in events)


class G3ModelSelection(unittest.TestCase):
    """T019's model rule: the preferred model, or a non-deprecated GPT-5.x, or nothing."""

    @classmethod
    def setUpClass(cls):
        cls.record_module = _load("g3_record", GATES / "G3" / "record.py")

    def select(self, rows):
        return self.record_module.select_model(models_table(rows))

    def test_01_the_preferred_model_is_selected_without_a_fallback(self):
        model, fallback, _ = self.select((("chatgpt", "gpt-5.6", True),))
        self.assertEqual(model, "gpt-5.6")
        self.assertFalse(fallback)

    def test_02_a_missing_preferred_model_falls_back_to_a_gpt5_model(self):
        model, fallback, why = self.select((("chatgpt", "gpt-5.2", True),
                                            ("chatgpt", "gpt-5.4", False)))
        self.assertEqual(model, "gpt-5.4")
        self.assertTrue(fallback)
        self.assertIn("fallback", why)

    def test_03_an_older_family_is_never_selected(self):
        """The gate must fail rather than quietly drop to a pre-GPT-5 model."""
        for rows in (
            (("chatgpt", "gpt-4o", True),),
            (("chatgpt", "gpt-4.1", True), ("chatgpt", "o3", False)),
            (("chatgpt", "gpt-3.5-turbo", True),),
        ):
            with self.subTest(rows=rows):
                model, _, why = self.select(rows)
                self.assertIsNone(model)
                self.assertIn("GPT-5.x", why)

    def test_04_a_deprecated_gpt5_model_is_rejected(self):
        model, _, _ = self.select((("chatgpt", "gpt-5.1-deprecated", True),))
        self.assertIsNone(model)

    def test_05_an_empty_or_unreadable_list_selects_nothing(self):
        for text in ("", "No models available.\n", "PROVIDER MODEL DEFAULT\n"):
            with self.subTest(text=text):
                model, _, _ = self.record_module.select_model(text)
                self.assertIsNone(model)

    def test_06_another_provider_is_never_selected(self):
        model, _, _ = self.record_module.select_model(
            "PROVIDER MODEL DEFAULT\nopenai gpt-5.6 *\n")
        self.assertIsNone(model)


class G3Recorder(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.record_module = _load("g3_record_main", GATES / "G3" / "record.py")
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
        self._write_work()

    def _write(self, name, document):
        (self.work / name).write_text(
            document if isinstance(document, str) else json.dumps(document), encoding="utf-8")

    def _vmstate(self, **overrides):
        values = {
            "config_dir_entries": "1",
            "config_dir_names": self.common.CREDENTIAL_FILE,
            "credential_present": "yes",
            "credential_mode": "600",
            "credential_owner": "agent:agent",
            "full_cagent_copied": "no",
            "stdin_is_tty": "no",
            "agent_binary": self.versions["docker_agent"],
        }
        values.update({f"apikey_{n}": "absent" for n in self.common.API_KEY_NAMES})
        values.update(overrides)
        return "".join(f"{k}={v}\n" for k, v in values.items())

    def _approval(self, exists, markers):
        return f"file_exists={exists}\nmarker_lines={markers}\nws_files=x\n"

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

        self._write("models.txt", models_table())
        # The backend rejects the listed default and accepts gpt-5.5, which is what the live run
        # observed. Enumeration stops at the first acceptance, so later candidates are unprobed.
        self._write("model-probe.txt", "gpt-5.6=1\ngpt-5.5=0\n")
        self._write("probe-gpt-5.6.err",
                    'Error: model failed: HTTP 400: {"detail":"The \'gpt-5.6\' model is not '
                    'supported when using Codex with a ChatGPT account."}\n')
        self._write("apikeys-host.txt",
                    "".join(f"{n}=absent\n" for n in self.common.API_KEY_NAMES))
        for slot in ("a", "b"):
            self._write(f"vmstate-{slot}.txt", self._vmstate())
            self._write(f"task-{slot}.json", run_capture())
            self._write(f"ls-before-{slot}.json", {"sandboxes": []})
            self._write(f"refresh-check-{slot}.json",
                        {"allowed": False, "deny_kind": "implicit",
                         "target": self.common.REFRESH_CANDIDATE + ":443"})
        self._write("ls-between-a.json", {"sandboxes": []})
        self._write("control-nocred.json", run_capture(include_answer=False))
        self._write("approval-allow.txt", self._approval("yes", "1"))
        self._write("approval-exit2.txt", self._approval("no", "0"))
        self._write("approval-no-decision.txt", self._approval("no", "0"))
        self._write("approval-hostile.txt", self._approval("no", "0"))
        self._write("approval-setup.txt",
                    "hostile_config_entries=chatgpt-auth.json,config.yaml\n"
                    "hostile_config_safety=autonomous\nhostile_config_yolo=true\n")

    def _capture(self, **overrides):
        obs = {
            "sbx_env_ssh_auth_sock": "removed",
            "pf_version_exit": "0", "pf_ssh_forwarding_exit": "0", "pf_ssh_socket_exit": "0",
            "pf_policy_exit": "0", "pf_ls_exit": "0",
            "governance_before_exit": "1", "governance_after_exit": "1",
            "ls_after_exit": "0", "policy_after_exit": "0", "templates_exit": "0",
            "sbx_resolved_base": self.common.codex_base(self.versions)[0],
            "policy_allow": ",".join(self.allow), "policy_deny": ",".join(self.deny),
            "policy_rules_exit": "0", "safety_flag": "strict",
            "models_exit": "0", "listed_preferred": "gpt-5.6",
            "runtime_selected_model": "gpt-5.5",
            "apikeys_host_exit": "0", "host_agent_version_exit": "0",
            "host_config_dir_entries": "4", "host_credential_mode": "600",
            "staged_entries": "1", "staged_names": self.common.CREDENTIAL_FILE,
            "staged_hostile_names": "chatgpt-auth.json,config.yaml",
            "credentials_scrubbed": "yes",
            "cp_probe_exit": "0", "cp_hooks_exit": "0", "cp_cred_hostile_exit": "0",
            "approval_setup_exit": "0", "control_nocred_exit": "1",
        }
        for slot in ("a", "b"):
            obs.update({
                f"ls_before_{slot}_exit": "0", f"create_dca-g3-{slot}_exit": "0",
                f"policy_rules_{slot}_exit": "0", f"refresh_check_{slot}_exit": "1",
                f"cp_cred_{slot}_exit": "0", f"cp_agent_{slot}_exit": "0",
                f"cp_tasks_{slot}_exit": "0", f"vmstate_{slot}_exit": "0",
                f"own_cred_{slot}_exit": "0",
                f"task_{slot}_exit": "0", f"rm_dca-g3-{slot}_exit": "0",
                f"ls_between_{slot}_exit": "0",
            })
        for case in ("allow", "exit2", "no-decision", "hostile"):
            obs[f"approval_{case}_exit"] = "0"
            obs[f"approval_state_{case}_exit"] = "0"
        obs.update(overrides)
        return obs

    def _results(self, obs=None):
        criteria, _, _ = self.record_module.evaluate(obs or self._capture(), str(self.work),
                                                    self.versions)
        return {row["id"]: row["result"] for row in criteria}

    def _record_to(self, obs=None):
        obs = obs or self._capture()
        obs_path = self.tmp / "observations.env"
        obs_path.write_text("".join(f"{k}={v}\n" for k, v in obs.items()), encoding="utf-8")
        evidence_path = self.tmp / "G3.json"
        status, _ = self.record_module.record(
            str(obs_path), str(self.work), str(ROOT / "runtime" / "versions.yaml"),
            str(evidence_path))
        return status, json.loads(evidence_path.read_text(encoding="utf-8"))

    # --- the clean run ------------------------------------------------------------------------------

    def test_01_a_clean_capture_passes_and_validates(self):
        status, evidence = self._record_to()
        self.assertEqual(status, "PASS", json.dumps(
            [c for c in evidence["criteria"] if c["result"] != "PASS"], indent=2))
        self.assertEqual(
            [e.message for e in
             jsonschema.Draft202012Validator(self.evidence_schema).iter_errors(evidence)], [])
        backend = evidence["codex_backend"]
        self.assertEqual(backend["provider"], "chatgpt")
        self.assertEqual(backend["selected_model"], "gpt-5.5")
        self.assertTrue(backend["model_fallback_applied"])
        self.assertEqual(backend["listed_models"], ["gpt-5.6"])
        self.assertEqual(backend["backend_accepted_models"], ["gpt-5.5"])
        self.assertEqual(backend["backend_rejected_models"], ["gpt-5.6"])
        self.assertIn("REJECTED", backend["model_selection_reason"])
        self.assertEqual(backend["safety"], "strict")
        self.assertTrue(backend["token_file_fallback_proven"])
        self.assertIs(backend["in_vm_refresh_required"], False)
        self.assertEqual(evidence["fallback_applied"], "model gpt-5.5")

    def test_02_no_secret_material_appears_in_the_evidence(self):
        """Value-shaped material, not vocabulary.

        The notes legitimately NAME the things they exclude ("no token value ... cookie ..."), so a
        substring check on those words would fail on correct evidence. What must never appear is
        anything token-SHAPED.
        """
        import re
        text = json.dumps(self._record_to()[1])
        patterns = {
            "bearer value": r"[Aa]uthorization\s*[:=]\s*\"?Bearer\s+\S{12,}",
            "oauth json value": r"\"(access_token|refresh_token|id_token)\"\s*:\s*\"[^\"]{12,}\"",
            "cookie value": r"[Cc]ookie\s*[:=]\s*\S{16,}=",
            "jwt": r"\beyJ[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}\.",
            "api key": r"\bsk-[A-Za-z0-9\-]{16,}",
            "long opaque blob": r"\"[A-Za-z0-9+/]{80,}={0,2}\"",
        }
        for label, pattern in patterns.items():
            with self.subTest(pattern=label):
                self.assertIsNone(re.search(pattern, text), label)

    # --- availability and the model ------------------------------------------------------------------

    def test_03_a_backend_that_accepts_nothing_fails(self):
        """Every candidate rejected means no usable non-deprecated GPT-5.x, so Codex is unavailable."""
        self._write("model-probe.txt",
                    "".join(f"{c}=1\n" for c in self.common.MODEL_CANDIDATES))
        results = self._results(self._capture(runtime_selected_model=""))
        self.assertEqual(results["G3.model"], "FAIL")

    def test_04_the_listing_is_not_the_authority(self):
        """A model the provider lists but the backend rejects must never be selected."""
        self._write("model-probe.txt", "gpt-5.6=1\n")
        self.assertEqual(self._results(self._capture(runtime_selected_model="gpt-5.6"))["G3.model"],
                         "FAIL")

    def test_04b_a_run_that_used_a_different_model_than_the_verified_one_fails(self):
        self._write("task-b.json", run_capture(model="gpt-5.6"))
        self.assertEqual(self._results()["G3.model"], "FAIL")

    def test_04c_a_run_that_did_not_use_the_native_chatgpt_provider_fails(self):
        self._write("task-a.json", run_capture(provider="openai"))
        self.assertEqual(self._results()["G3.model"], "FAIL")

    def test_04d_an_unprobed_backend_is_never_a_pass(self):
        (self.work / "model-probe.txt").unlink()
        self.assertEqual(self._results()["G3.model"], "FAIL")

    def test_05_a_failed_model_listing_fails(self):
        self.assertEqual(self._results(self._capture(models_exit="1"))["G3.model"], "FAIL")

    def test_06_a_provider_api_key_on_the_host_fails(self):
        self._write("apikeys-host.txt", "OPENAI_API_KEY=PRESENT\n")
        self.assertEqual(self._results()["G3.no-api-key"], "FAIL")

    def test_06b_a_base_declared_variable_is_recorded_not_treated_as_a_key(self):
        """An empty or base-declared variable is not V1 introducing a key.

        The decisive property is the negative control, so a declared variable must be RECORDED
        rather than turned into a failure the gate cannot act on.
        """
        for classification in ("empty", "present"):
            with self.subTest(classification=classification):
                self._write("vmstate-a.txt",
                            self._vmstate(apikey_OPENAI_API_KEY=classification))
                self._write("vmstate-b.txt",
                            self._vmstate(apikey_OPENAI_API_KEY=classification))
                results = self._results()
                self.assertEqual(results["G3.no-api-key"], "PASS")
                self.setUp()

    def test_06c_a_negative_control_that_succeeded_fails_the_gate(self):
        """If the task works without the credential, something else authenticates it."""
        with self.subTest("exit 0"):
            self.assertEqual(
                self._results(self._capture(control_nocred_exit="0"))["G3.no-api-key"], "FAIL")
        with self.subTest("marker present"):
            self._write("control-nocred.json", run_capture())
            self.assertEqual(self._results()["G3.no-api-key"], "FAIL")

    def test_06d_a_missing_negative_control_is_never_a_pass(self):
        obs = self._capture()
        del obs["control_nocred_exit"]
        self.assertEqual(self._results(obs)["G3.no-api-key"], "FAIL")

    def test_06e_an_unpinned_in_vm_binary_fails(self):
        """The binary self-updates at startup, so the version it reports is what proves the pin."""
        self._write("vmstate-b.txt", self._vmstate(agent_binary="v1.999.0"))
        self.assertEqual(self._results()["G3.pinned-binary"], "FAIL")

    # --- the minimal credential ----------------------------------------------------------------------

    def test_07_a_full_cagent_copy_fails(self):
        """Copying the whole config dir would bring .env and cached state with the token."""
        self._write("vmstate-a.txt", self._vmstate(full_cagent_copied="yes"))
        self.assertEqual(self._results()["G3.minimal-credential"], "FAIL")

    def test_08_more_than_one_entry_in_the_config_dir_fails(self):
        for where, change in (("staged", {"staged_entries": "3"}),
                              ("staged names", {"staged_names": "chatgpt-auth.json,config.yaml"})):
            with self.subTest(where=where):
                self.assertEqual(
                    self._results(self._capture(**change))["G3.minimal-credential"], "FAIL")
        self._write("vmstate-a.txt", self._vmstate(config_dir_entries="2",
                                                   config_dir_names="chatgpt-auth.json,other"))
        self.assertEqual(self._results()["G3.minimal-credential"], "FAIL")

    def test_09_an_absent_credential_fails(self):
        self._write("vmstate-b.txt", self._vmstate(credential_present="no"))
        self.assertEqual(self._results()["G3.minimal-credential"], "FAIL")

    def test_10_a_credential_left_behind_fails_hygiene(self):
        self.assertEqual(
            self._results(self._capture(credentials_scrubbed="no"))["G3.credential-hygiene"],
            "FAIL")

    # --- two consecutive fresh sandboxes --------------------------------------------------------------

    def test_11_the_second_sandbox_failing_fails_the_gate(self):
        status, _ = self._record_to(self._capture(task_b_exit="1"))
        self.assertEqual(status, "FAIL")
        self.assertEqual(self._results(self._capture(task_b_exit="1"))["G3.fresh-sandboxes"],
                         "FAIL")

    def test_12_a_run_with_no_marker_answer_fails(self):
        self._write("task-b.json", run_capture(include_answer=False))
        self.assertEqual(self._results()["G3.fresh-sandboxes"], "FAIL")

    def test_13_a_tty_means_the_run_was_not_non_interactive(self):
        self._write("vmstate-a.txt", self._vmstate(stdin_is_tty="yes"))
        self.assertEqual(self._results()["G3.fresh-sandboxes"], "FAIL")

    def test_14_a_sandbox_created_while_another_existed_is_not_fresh(self):
        self._write("ls-before-b.json", {"sandboxes": [{"name": "dca-g3-a"}]})
        self.assertEqual(self._results()["G3.fresh-sandboxes"], "FAIL")

    def test_15_a_first_sandbox_surviving_its_removal_fails(self):
        self._write("ls-between-a.json", {"sandboxes": [{"name": "dca-g3-a"}]})
        self.assertEqual(self._results()["G3.fresh-sandboxes"], "FAIL")

    # --- the approval pipeline, the fail-open cases ----------------------------------------------------

    def test_16_an_allowed_write_that_did_not_happen_fails(self):
        self._write("approval-allow.txt", self._approval("no", "0"))
        self.assertEqual(self._results()["G3.approval-allow"], "FAIL")

    def test_17_an_allowed_write_that_happened_twice_fails(self):
        self._write("approval-allow.txt", self._approval("yes", "2"))
        self.assertEqual(self._results()["G3.approval-allow"], "FAIL")

    def test_18_an_exit_2_hook_that_did_not_block_fails(self):
        """The pipeline failing open is the single most important thing this gate can catch."""
        self._write("approval-exit2.txt", self._approval("yes", "1"))
        results = self._results()
        self.assertEqual(results["G3.approval-exit2"], "FAIL")

    def test_19_no_decision_treated_as_allow_fails(self):
        self._write("approval-no-decision.txt", self._approval("yes", "1"))
        self.assertEqual(self._results()["G3.approval-no-decision"], "FAIL")

    def test_20_autonomous_and_yolo_defeating_strict_fails(self):
        self._write("approval-hostile.txt", self._approval("yes", "1"))
        results = self._results()
        self.assertEqual(results["G3.approval-hostile"], "FAIL")
        self.assertEqual(results["G3.strict"], "FAIL")

    def test_21_a_hostile_config_that_never_asked_for_the_weaker_settings_proves_nothing(self):
        self._write("approval-setup.txt",
                    "hostile_config_safety=strict\nhostile_config_yolo=false\n")
        self.assertEqual(self._results()["G3.strict"], "FAIL")

    def test_22_a_safety_flag_other_than_strict_fails(self):
        for flag in ("balanced", "restricted", "autonomous", ""):
            with self.subTest(flag=flag):
                self.assertEqual(self._results(self._capture(safety_flag=flag))["G3.strict"],
                                 "FAIL")

    def test_23_an_approval_case_that_never_ran_is_never_a_pass(self):
        obs = self._capture()
        del obs["approval_no-decision_exit"]
        self.assertEqual(self._results(obs)["G3.approval-no-decision"], "NOT-RUN")

    # --- refresh is observed, never assumed -----------------------------------------------------------

    def test_24_a_refresh_host_that_was_not_denied_proves_nothing(self):
        self._write("refresh-check-b.json", {"allowed": True})
        self.assertEqual(self._results()["G3.refresh"], "FAIL")

    def test_25_the_recorded_refresh_state_is_the_committed_inventorys(self):
        _, evidence = self._record_to()
        candidate = evidence["codex_backend"]["refresh_candidate"]
        self.assertEqual(candidate["host"], "auth.openai.com")
        self.assertEqual(candidate["purpose"], "host-oauth-login")
        self.assertIs(candidate["sandbox_required"], False)
        self.assertEqual(candidate["profiles"], [])

    # --- bindings and cleanup -------------------------------------------------------------------------

    def test_26_a_base_mismatch_fails_and_stops_the_run(self):
        self.assertEqual(self._results(self._capture(
            sbx_resolved_base="docker/sandbox-templates:claude-code-docker"))["G3.base"], "FAIL")

    def test_27_a_policy_that_is_not_the_accepted_one_fails(self):
        for change in ({"policy_allow": "chatgpt.com"},
                       {"policy_allow": ",".join(self.allow + ["auth.openai.com"])},
                       {"policy_rules_exit": "1"}):
            with self.subTest(change=change):
                self.assertEqual(self._results(self._capture(**change))["G3.policy"], "FAIL")

    def test_28_a_cleanup_failure_fails(self):
        for change in ({"rm_dca-g3-a_exit": "1"}, {"rm_dca-g3-b_exit": "1"}):
            with self.subTest(change=change):
                self.assertEqual(self._results(self._capture(**change))["G3.clean"], "FAIL")
        self._write("ls-after.json", {"sandboxes": [{"name": "dca-g3-b"}]})
        self.assertEqual(self._results()["G3.clean"], "FAIL")

    def test_29_a_changed_global_policy_fails_the_fingerprint(self):
        self._write("policy-after.json", {"rules": []})
        self.assertEqual(self._results()["G3.fingerprint"], "FAIL")

    def test_30_stale_provenance_is_never_a_pass(self):
        changed = dict(self.versions)
        changed["docker_agent"] = "v1.999.0"
        obs_path = self.tmp / "observations.env"
        obs_path.write_text("".join(f"{k}={v}\n" for k, v in self._capture().items()),
                            encoding="utf-8")
        criteria, _, _ = self.record_module.evaluate(self._capture(), str(self.work), changed)
        self.assertTrue(any(row["result"] != "PASS" for row in criteria))


if __name__ == "__main__":
    unittest.main()
