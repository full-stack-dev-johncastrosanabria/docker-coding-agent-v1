"""Contract tests for the G0 recorder (tasks.md T008, gates/G0/record.py).

They run the recorder against synthetic sbx output in a temporary work directory: no sandbox,
no sbx call and no change to the real runtime/versions.yaml. The shapes mirror what the
observed sbx v0.43.0 candidate emits (see the recorder's docstring), including the
authenticated `sbx ls --json` shape {"sandboxes": [...]} captured read-only on this host.

Run in the development environment (requirements-dev.txt): jsonschema is used here to check
that the evidence the gate writes satisfies gates/evidence.schema.json.
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
SSH_KEY = "ssh.agentForwardingEnabled"
# Exactly what the observed sbx v0.43.0 candidate writes to stderr (123 bytes), with exit 1
# and empty stdout, when the global network policy is uninitialized.
POLICY_UNINITIALIZED_TEXT = (
    "ERROR: global network policy has not been initialized\n"
    "\n"
    "Initialize it with:\n"
    "  sbx policy init <allow-all|balanced|deny-all>\n"
)


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _diagnose(**statuses):
    names = {"daemon": "Daemon", "version_match": "Version match", "authentication": "Authentication"}
    checks = [
        {"name": names[key], "status": status, "message": f"synthetic {status}", "detail": "", "hint": ""}
        for key, status in statuses.items()
    ]
    return {"version": "1.0", "checks": checks, "summary": {"pass": 0, "warn": 0, "fail": 0, "skip": 0}}


class G0Recorder(unittest.TestCase):
    """Each test starts from a work directory where every criterion passes, then breaks one."""

    @classmethod
    def setUpClass(cls):
        cls.record_module = _load("g0_record", GATES / "G0" / "record.py")
        cls.evidence_schema = json.loads((GATES / "evidence.schema.json").read_text(encoding="utf-8"))
        cls.rules = _load("eligibility_rules", GATES / "eligibility_rules.py")

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.work = self.tmp / "work"
        self.work.mkdir()
        self.versions_path = self.tmp / "versions.yaml"
        shutil.copy(ROOT / "runtime" / "versions.yaml", self.versions_path)
        # Snapshot: a gate that doesn't pass must leave this file byte-identical, whatever
        # pins a previous passing G0 already wrote into the real file.
        self.versions_before = self.versions_path.read_text(encoding="utf-8")
        self.evidence_path = self.tmp / "G0.json"

        self.files = {
            "version.json": {
                "client": {"version": "v0.43.0", "revision": "synthetic"},
                "server": {"state": "running", "version": "v0.43.0", "api_version": "0.31.0"},
            },
            "diagnose.json": _diagnose(daemon="pass", version_match="pass", authentication="pass"),
            "diagnose-after.json": _diagnose(daemon="pass", version_match="pass", authentication="pass"),
            "ls.json": {"sandboxes": []},
            "settings-before.json": {"key": SSH_KEY, "value": True, "source": "default"},
            "settings-after.json": {"key": SSH_KEY, "value": False, "source": "override"},
            "policy-after.json": {"policies": []},
        }
        self.texts = {"policy-before.json": "", "policy-before.err": POLICY_UNINITIALIZED_TEXT}
        self.obs = {
            "docker_server": "29.8.0",
            "sbx_present": "true",
            "version_exit": "0",
            "diagnose_exit": "1",  # diagnose exits non-zero on any failing check; checks decide
            "docker_agent": "v1.136.0",
            "claude_code": "2.1.278",
            "git": "2.54.0",
            "ls_exit": "0",
            "settings_before_exit": "0",
            "settings_set_exit": "0",
            "daemon_restart_exit": "0",
            "settings_after_exit": "0",
            "diagnose_after_exit": "1",
            "policy_before_exit": "1",  # sbx reports the uninitialized policy as an error
            "policy_init_exit": "0",
            "policy_init_preset": "deny-all",
            "policy_previous_state": "uninitialized",
            "policy_after_exit": "0",
        }

    # --- helpers ---------------------------------------------------------------------------

    def write(self):
        for name, document in self.files.items():
            (self.work / name).write_text(json.dumps(document), encoding="utf-8")
        for name, text in self.texts.items():
            (self.work / name).write_text(text, encoding="utf-8")
        obs_path = self.tmp / "observations.env"
        obs_path.write_text("".join(f"{k}={v}\n" for k, v in self.obs.items()), encoding="utf-8")
        return obs_path

    def run_recorder(self):
        return self.record_module.record(
            str(self.write()), str(self.work), str(self.versions_path), str(self.evidence_path)
        )

    def results(self):
        status, criteria = self.run_recorder()
        return status, {row["id"]: row["result"] for row in criteria}

    def assert_fails(self, criterion):
        status, results = self.results()
        self.assertEqual(status, "FAIL")
        self.assertEqual(results[criterion], "FAIL")
        # A gate that didn't pass never pins the environment it failed on (a step-1 failure
        # leaves the later criteria NOT-RUN, which is equally not a pass).
        self.assertIn(results["G0.9"], ("FAIL", "NOT-RUN"))
        self.assertEqual(self.versions_path.read_text(encoding="utf-8"), self.versions_before)

    def may_change_globals(self):
        """Whether the PRE-SSH guard would let the SSH mutation proceed."""
        return self.guard("--ready")

    def guard(self, mode):
        """Whether one of run.sh's guards would let the run continue."""
        obs_path = str(self.write())
        return self.record_module.main(["record.py", mode, obs_path, str(self.work)]) == 0

    def drop_ssh_observations(self):
        """The state at the PRE-SSH guard: nothing of step 2 has run yet."""
        for name in ("settings-before.json", "settings-after.json", "diagnose-after.json"):
            self.files.pop(name, None)
        for key in ("settings_before_exit", "settings_set_exit", "daemon_restart_exit",
                    "settings_after_exit", "diagnose_after_exit"):
            self.obs.pop(key, None)

    # --- the passing baseline --------------------------------------------------------------

    def test_everything_passing_writes_pins_and_valid_evidence(self):
        # Seed values the run must overwrite, so this proves the recorder writes the observed
        # pins rather than inheriting whatever the real versions.yaml already holds.
        seeded = json.loads(self.versions_before)
        seeded["sbx"]["exact"] = "0.0.0-stale"
        seeded["claude_code"]["exact"] = "0.0.0-stale"
        self.versions_path.write_text(
            json.dumps(seeded, indent=2, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8"
        )
        self.versions_before = self.versions_path.read_text(encoding="utf-8")

        status, results = self.results()
        self.assertEqual(status, "PASS", results)
        self.assertTrue(all(result == "PASS" for result in results.values()), results)

        versions = json.loads(self.versions_path.read_text(encoding="utf-8"))
        self.assertEqual(versions["sbx"]["exact"], "v0.43.0")
        self.assertEqual(versions["claude_code"]["exact"], "2.1.278")
        # Decided values are never touched by G0.
        self.assertEqual(versions["sbx"]["minimum"], "0.43.0")
        self.assertEqual(versions["claude_code"]["tested"], "2.1.277")
        # Canonical JSON-compatible YAML form is preserved.
        self.assertEqual(
            self.versions_path.read_text(encoding="utf-8"),
            json.dumps(versions, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        )

        evidence = json.loads(self.evidence_path.read_text(encoding="utf-8"))
        self.assertEqual(
            [e.message for e in jsonschema.Draft202012Validator(self.evidence_schema).iter_errors(evidence)],
            [],
        )
        # The provenance describes the file as it stands after the pins were written.
        self.assertEqual(evidence["provenance"], self.rules.evidence_provenance("G0", versions))
        self.assertEqual(self.rules.evidence_problems(evidence, versions), [])
        self.assertTrue(self.may_change_globals())

    # --- H1: the SSH change must have taken effect ------------------------------------------

    def test_daemon_restart_failure_fails_even_when_the_stored_setting_is_false(self):
        self.obs["daemon_restart_exit"] = "1"
        self.assert_fails("G0.7")

    def test_settings_set_failure_fails(self):
        self.obs["settings_set_exit"] = "1"
        self.assert_fails("G0.7")

    def test_settings_get_failure_fails(self):
        self.obs["settings_before_exit"] = "1"
        self.assert_fails("G0.7")
        self.setUp()
        self.obs["settings_after_exit"] = "1"
        self.assert_fails("G0.7")

    def test_malformed_settings_json_fails(self):
        for name in ("settings-before.json", "settings-after.json"):
            with self.subTest(file=name):
                self.setUp()
                del self.files[name]
                self.texts[name] = "not json at all"
                self.assert_fails("G0.7")

    def test_non_boolean_before_value_fails(self):
        self.files["settings-before.json"] = {"key": SSH_KEY, "value": "true"}
        self.assert_fails("G0.7")

    def test_unhealthy_daemon_after_restart_fails(self):
        self.files["diagnose-after.json"] = _diagnose(daemon="fail")
        self.assert_fails("G0.7")

    # --- the post-SSH guard: no second global change once the SSH step failed ---------------

    def test_post_ssh_guard_passes_only_when_the_ssh_change_is_proven(self):
        self.assertTrue(self.guard("--post-ssh"))

    def test_post_ssh_guard_blocks_the_policy_bootstrap_after_an_ssh_failure(self):
        cases = {
            "settings set failed": lambda: self.obs.__setitem__("settings_set_exit", "1"),
            "daemon restart failed": lambda: self.obs.__setitem__("daemon_restart_exit", "1"),
            "settings re-read failed": lambda: self.obs.__setitem__("settings_after_exit", "1"),
            "forwarding still enabled": lambda: self.files.__setitem__(
                "settings-after.json", {"key": SSH_KEY, "value": True}
            ),
            "daemon unhealthy after restart": lambda: self.files.__setitem__(
                "diagnose-after.json", _diagnose(daemon="fail")
            ),
        }
        for label, break_it in cases.items():
            with self.subTest(case=label):
                self.setUp()
                break_it()
                # The PRE-SSH guard already passed by then; the POST-SSH guard is what stops it.
                self.assertTrue(self.guard("--ready"))
                self.assertFalse(self.guard("--post-ssh"))
                status, results = self.results()
                self.assertEqual(status, "FAIL")
                self.assertEqual(results["G0.7"], "FAIL")
                # The policy bootstrap is never reached, so it is NOT-RUN, not a failure of its
                # own, and no pin is written.
                self.assertEqual(results["G0.8"], "NOT-RUN")
                self.assertEqual(results["G0.9"], "NOT-RUN")
                self.assertEqual(self.versions_path.read_text(encoding="utf-8"), self.versions_before)

    def test_run_sh_calls_the_guards_in_the_fail_closed_order(self):
        # The guards only protect anything if the procedure actually calls them, in order:
        # preconditions -> SSH mutation -> proof it took effect -> policy bootstrap.
        script = (GATES / "G0" / "run.sh").read_text(encoding="utf-8")
        body = "\n".join(line for line in script.splitlines() if not line.lstrip().startswith("#"))

        def at(needle):
            index = body.find(needle)
            self.assertNotEqual(index, -1, f"{needle!r} is missing from run.sh")
            return index

        order = [
            "record.py --step1",
            "sbx ls --json",
            "record.py --ready",
            "sbx settings set ssh.agentForwardingEnabled false",
            "sbx daemon restart",
            "record.py --post-ssh",
            "sbx policy ls --json",
            "sbx policy init deny-all",
        ]
        positions = [at(step) for step in order]
        for earlier, later, before, after in zip(order, order[1:], positions, positions[1:]):
            self.assertLess(before, after, f"{earlier!r} must come before {later!r} in run.sh")
        # The bootstrap runs only inside the uninitialized branch.
        self.assertLess(at("record.py --policy-uninitialized"), at("sbx policy init deny-all"))
        # Every guard failure takes the stop path rather than continuing.
        self.assertEqual(body.count("|| stop"), 3)

    def test_pre_ssh_guard_keeps_its_own_meaning(self):
        # Before step 2 runs there are no SSH observations at all: --ready must still pass
        # (that is what lets the SSH change start), while --post-ssh must not.
        self.drop_ssh_observations()
        self.assertTrue(self.guard("--ready"))
        self.assertFalse(self.guard("--post-ssh"))
        # And it still refuses when its own preconditions fail.
        self.setUp()
        self.drop_ssh_observations()
        self.files["ls.json"] = {"sandboxes": [{"name": "s1"}]}
        self.assertFalse(self.guard("--ready"))
        self.setUp()
        self.drop_ssh_observations()
        self.files["diagnose.json"] = _diagnose(daemon="pass", version_match="pass", authentication="fail")
        self.assertFalse(self.guard("--ready"))
        self.assertFalse(self.guard("--step1"))

    # --- M1: a healthy, matching daemon -----------------------------------------------------

    def test_unhealthy_daemon_fails_and_blocks_the_global_change(self):
        self.files["diagnose.json"] = _diagnose(daemon="fail", version_match="pass", authentication="pass")
        self.assert_fails("G0.4")
        self.assertFalse(self.may_change_globals())

    def test_cli_daemon_version_mismatch_fails(self):
        self.files["diagnose.json"] = _diagnose(daemon="pass", version_match="fail", authentication="pass")
        self.assert_fails("G0.4")
        self.assertFalse(self.may_change_globals())

    def test_missing_diagnose_check_fails(self):
        self.files["diagnose.json"] = _diagnose(daemon="pass", authentication="pass")
        self.assert_fails("G0.4")

    def test_server_below_minimum_fails(self):
        self.files["version.json"]["server"]["version"] = "v0.42.0"
        self.assert_fails("G0.3")
        self.assertFalse(self.may_change_globals())

    def test_server_not_running_fails(self):
        self.files["version.json"]["server"] = {"state": "unavailable", "error": "daemon not running"}
        self.assert_fails("G0.3")

    def test_client_below_minimum_fails(self):
        self.files["version.json"]["client"]["version"] = "v0.42.0"
        self.assert_fails("G0.3")

    # --- authentication ---------------------------------------------------------------------

    def test_authentication_failure_blocks_every_global_change(self):
        self.files["diagnose.json"] = _diagnose(daemon="pass", version_match="pass", authentication="fail")
        status, results = self.results()
        self.assertEqual(status, "FAIL")
        self.assertEqual(results["G0.5"], "FAIL")
        # Nothing past step 1 is even evaluated, and neither guard lets the mutation proceed.
        for later in ("G0.6", "G0.7", "G0.8", "G0.9"):
            self.assertEqual(results[later], "NOT-RUN")
        obs_path = str(self.write())
        self.assertEqual(self.record_module.main(["record.py", "--step1", obs_path, str(self.work)]), 1)
        self.assertEqual(self.record_module.main(["record.py", "--ready", obs_path, str(self.work)]), 1)

    # --- M2: no sandbox running --------------------------------------------------------------

    def test_any_listed_sandbox_blocks_the_global_change(self):
        # The authenticated `sbx ls --json` shape hasn't been observed, so G0 keeps the stricter
        # zero-sandbox rule: a stopped sandbox blocks the change rather than being guessed at.
        for label, listing in (
            ("running", {"sandboxes": [{"name": "s1", "status": "running"}]}),
            ("stopped", {"sandboxes": [{"name": "s1", "status": "stopped"}]}),
        ):
            with self.subTest(listing=label):
                self.setUp()
                self.files["ls.json"] = listing
                self.assert_fails("G0.6")
                self.assertFalse(self.may_change_globals())

    def test_only_the_observed_listing_shape_is_accepted(self):
        # A wrapper object that happens to hold one list is not evidence that nothing runs, and
        # neither is a document that gained a top-level key the capture didn't have.
        for listing in (
            {"warnings": []},
            {"errors": []},
            {"anything": []},
            {},
            [],
            [{"name": "s1"}],
            {"sandboxes": [], "warnings": []},
            {"sandboxes": "none"},
        ):
            with self.subTest(listing=listing):
                self.setUp()
                self.files["ls.json"] = listing
                self.assertIsNone(self.record_module.sandbox_count(listing))
                self.assert_fails("G0.6")
                self.assertFalse(self.may_change_globals())

    def test_unexpected_or_unreadable_listing_fails_closed(self):
        self.setUp()
        del self.files["ls.json"]
        self.texts["ls.json"] = "ERROR: 401 Unauthorized"
        self.assert_fails("G0.6")

    # --- exit status on the deterministic surfaces --------------------------------------------

    def test_unsuccessful_version_command_fails(self):
        self.obs["version_exit"] = "1"
        self.assert_fails("G0.2")
        self.assertFalse(self.may_change_globals())

    def test_unsuccessful_ls_command_fails(self):
        self.obs["ls_exit"] = "1"
        self.assert_fails("G0.6")
        self.assertFalse(self.may_change_globals())

    def test_unsuccessful_policy_read_after_init_fails(self):
        self.obs["policy_after_exit"] = "1"
        self.assert_fails("G0.8")

    def test_existing_policy_with_an_unsuccessful_read_fails(self):
        # An initialized policy: the read succeeds and prints the document, with no error.
        self.texts["policy-before.err"] = ""
        del self.texts["policy-before.json"]
        self.files["policy-before.json"] = {"policies": [{"id": "synthetic"}]}
        self.obs["policy_before_exit"] = "0"
        del self.obs["policy_init_exit"]
        del self.obs["policy_init_preset"]
        self.obs["policy_before_exit"] = "1"  # the read itself failed
        self.assert_fails("G0.8")

    def test_diagnose_exit_code_never_decides(self):
        # diagnose exits non-zero whenever any check fails, including ones outside G0's set
        # (the disk-space warning, for instance). Only its named checks decide.
        self.obs["diagnose_exit"] = "7"
        self.obs["diagnose_after_exit"] = "7"
        status, results = self.results()
        self.assertEqual(status, "PASS", results)

    # --- H2: bootstrap network policy --------------------------------------------------------

    def test_uninitialized_policy_takes_only_the_deny_all_bootstrap_path(self):
        self.write()
        self.assertTrue(self.record_module.policy_uninitialized(str(self.work), self.obs))
        for preset in ("balanced", "allow-all"):
            with self.subTest(preset=preset):
                self.setUp()
                self.obs["policy_init_preset"] = preset
                self.assert_fails("G0.8")
        self.setUp()
        self.obs["policy_init_exit"] = "1"
        self.assert_fails("G0.8")

    def test_existing_policy_is_recorded_and_never_overwritten(self):
        self.setUp()
        # An initialized policy: the read succeeds and prints the document, with no error.
        self.texts["policy-before.err"] = ""
        del self.texts["policy-before.json"]
        self.files["policy-before.json"] = {"policies": [{"id": "synthetic"}]}
        self.obs["policy_before_exit"] = "0"
        del self.obs["policy_init_exit"]
        del self.obs["policy_init_preset"]
        self.obs["policy_previous_state"] = "initialized"
        status, results = self.results()
        self.assertEqual(status, "PASS", results)
        self.assertFalse(self.record_module.policy_uninitialized(str(self.work), self.obs))
        evidence = json.loads(self.evidence_path.read_text(encoding="utf-8"))
        observed = next(row["evidence_ref"] for row in evidence["criteria"] if row["id"] == "G0.8")
        self.assertIn("left unchanged for G4", observed)

    def test_initializing_over_an_existing_policy_fails(self):
        # An initialized policy: the read succeeds and prints the document, with no error.
        self.texts["policy-before.err"] = ""
        del self.texts["policy-before.json"]
        self.files["policy-before.json"] = {"policies": [{"id": "synthetic"}]}
        self.obs["policy_before_exit"] = "0"
        self.obs["policy_previous_state"] = "initialized"
        self.obs["policy_init_exit"] = "0"
        self.obs["policy_init_preset"] = "deny-all"
        self.assert_fails("G0.8")

    def test_unreadable_policy_output_fails_closed(self):
        self.setUp()
        self.texts["policy-before.err"] = "ERROR: 401 Unauthorized: user is not authenticated\n"
        self.assert_fails("G0.8")

    def test_only_the_exact_uninitialized_representation_authorizes_the_bootstrap(self):
        exact = POLICY_UNINITIALIZED_TEXT
        cases = {
            "reworded": exact.replace("has not been", "is not"),
            "no trailing newline": exact.rstrip("\n"),
            "leading whitespace": " " + exact,
            "extra line": exact + "note: something else\n",
            "different indent": exact.replace("  sbx policy init", "    sbx policy init"),
            "uppercase": exact.upper(),
            "401": "ERROR: 401 Unauthorized: user is not authenticated to Docker\n",
            "empty": "",
        }
        for label, stderr in cases.items():
            with self.subTest(stderr=label):
                self.setUp()
                self.texts["policy-before.err"] = stderr
                self.write()
                # Never recognized, so `sbx policy init` is never authorized...
                self.assertFalse(self.record_module.policy_uninitialized(str(self.work), self.obs))
                self.assertEqual(
                    self.record_module.main(
                        ["record.py", "--policy-uninitialized", str(self.tmp / "observations.env"),
                         str(self.work)]
                    ),
                    1,
                )
                # ...and, with no policy document either, the criterion fails closed.
                self.assert_fails("G0.8")

    def test_uninitialized_message_with_the_wrong_exit_or_stdout_fails_closed(self):
        for label, change in (
            ("exit 0", lambda: self.obs.__setitem__("policy_before_exit", "0")),
            ("exit 2", lambda: self.obs.__setitem__("policy_before_exit", "2")),
            ("stdout not empty", lambda: self.texts.__setitem__("policy-before.json", "{}\n")),
        ):
            with self.subTest(case=label):
                self.setUp()
                change()
                self.write()
                self.assertFalse(self.record_module.policy_uninitialized(str(self.work), self.obs))
                self.assert_fails("G0.8")

    def test_governance_is_never_invented(self):
        self.setUp()
        status, _ = self.results()
        self.assertEqual(status, "PASS")
        evidence = json.loads(self.evidence_path.read_text(encoding="utf-8"))
        observed = next(row["evidence_ref"] for row in evidence["criteria"] if row["id"] == "G0.8")
        self.assertIn("governance=unknown/not observable", observed)

        self.setUp()
        self.files["policy-after.json"] = {"policies": [], "governance": "Managed by acme"}
        self.results()
        evidence = json.loads(self.evidence_path.read_text(encoding="utf-8"))
        observed = next(row["evidence_ref"] for row in evidence["criteria"] if row["id"] == "G0.8")
        self.assertIn("governance=Managed by acme", observed)

    # --- pins ---------------------------------------------------------------------------------

    def test_pins_are_written_only_after_every_other_criterion_passes(self):
        for label, break_it in (
            ("ssh", lambda: self.obs.__setitem__("daemon_restart_exit", "1")),
            ("policy", lambda: self.obs.__setitem__("policy_init_preset", "balanced")),
            ("sandboxes", lambda: self.files.__setitem__("ls.json", {"sandboxes": [{"name": "s1"}]})),
        ):
            with self.subTest(broken=label):
                self.setUp()
                break_it()
                status, _ = self.results()
                self.assertEqual(status, "FAIL")
                self.assertEqual(self.versions_path.read_text(encoding="utf-8"), self.versions_before)

    def test_evidence_records_no_secret_like_field(self):
        self.results()
        evidence = json.loads(self.evidence_path.read_text(encoding="utf-8"))
        text = json.dumps(evidence).lower()
        for word in ("token", "secret", "password", "authorization", "bearer", "cookie"):
            self.assertNotIn(word, text)


if __name__ == "__main__":
    unittest.main()
