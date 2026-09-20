"""Contract tests for the G10 recorder (tasks.md T012, gates/G10/record.py).

They run the decision logic against synthetic captures in a temporary work directory: no
sandbox, no sbx command, no git fixture, no change to the real runtime/versions.yaml.

G10 proves two independent properties and neither may be weakened for the other, so both have
their own positive and negative cases here: Phase A refuses a dirty tree before anything is
created (and never on ignored files alone), and Phase B delivers only the selected committed
branch state into a mountless sandbox.
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
SANDBOX = "dca-g10-source"
SELECTED_REF = "refs/heads/dca-g10-selected"
SELECTED_SHA = "1" * 40
SECOND_SHA = "2" * 40
OVERRIDE = "gate-local --ignore-uncommitted equivalent, explicitly exercised"
# The decision matrix gates/G10/dirty_tree.sh must produce.
MATRIX = {
    "tracked_only": ("refuse", "3", "1", "0"),
    "untracked_only": ("refuse", "3", "0", "1"),
    "both": ("refuse", "3", "1", "1"),
    "ignored_only": ("allow", "0", "0", "0"),
    "clean": ("allow", "0", "0", "0"),
}
REFUSED = [case for case, (decision, *_) in MATRIX.items() if decision == "refuse"]


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class G10Recorder(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.record_module = _load("g10_record", GATES / "G10" / "record.py")
        cls.rules = _load("eligibility_rules", GATES / "eligibility_rules.py")
        cls.evidence_schema = json.loads((GATES / "evidence.schema.json").read_text(encoding="utf-8"))

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.work = self.tmp / "work"
        self.work.mkdir()
        self.evidence_path = self.tmp / "G10.json"
        self.versions_path = ROOT / "runtime" / "versions.yaml"
        pins = json.loads(self.versions_path.read_text(encoding="utf-8"))
        base = pins["sandbox_bases"]["claude"]["base"]
        digest = pins["sandbox_bases"]["claude"]["version"]
        repository, _, tag = base.rpartition(":")

        self.probe = {
            "clone_exit": "0",
            "checkout_exit": "0",
            "source_mount_path_exists": "no",
            "workspace_mounts": "0",
            "host_fs_mount_targets": "/etc/resolv.conf /etc/hosts",
            "mount_total": "18",
            "head_commit": SELECTED_SHA,
            "selected_commit_present": "yes",
            "second_commit_present": "no",
            "second_ref_hits": "0",
            "second_in_rev_list": "0",
            "ref_names": "refs/heads/dca/g10-run refs/remotes/origin/dca-g10-selected",
            "baseline_canary_hits": "1",
            "env_canary_hits": "0",
            "untracked_canary_hits": "0",
            "dirty_canary_hits": "0",
            "second_canary_hits": "0",
            "env_file_present": "no",
            "untracked_file_present": "no",
            "branch_only_file_present": "no",
            "probe_complete": "yes",
        }
        self.files = {
            "pf-version.json": {
                "client": {"version": pins["sbx"]["exact"]},
                "server": {"state": "running", "version": pins["sbx"]["exact"]},
            },
            "pf-ssh-forwarding.json": {
                "key": "ssh.agentForwardingEnabled", "value": False, "source": "override"
            },
            "pf-ssh-socket.json": {"key": "ssh.agentSocketPath", "value": "", "source": "default"},
            "pf-policy.json": {"rules": [{
                "id": "default-deny-all", "scope": "global", "applies_to": "all",
                "resource_type": "network", "decision": "deny", "resources": ["**"],
                "origin": "local", "layer": "local", "status": "active",
            }]},
            "templates.json": {"images": [{
                "id": digest.removeprefix("sha256:")[:12],
                "repository": f"docker.io/{repository}", "tag": tag, "size": 1,
            }]},
            "ls-baseline.json": {"sandboxes": []},
            "ls-after.json": {"sandboxes": []},
        }
        for case in MATRIX:
            self.files[f"ls-decide-{case}.json"] = {"sandboxes": []}
        self.obs = {
            "sbx_env_ssh_auth_sock": "removed",
            "pf_version_exit": "0", "pf_ssh_forwarding_exit": "0", "pf_ssh_socket_exit": "0",
            "pf_policy_exit": "0", "pf_ls_exit": "0", "template_ls_exit": "0",
            "canary_ids": "baseline env-ignored untracked dirty-edit second-branch",
            # Phase A: the decision matrix, plus the side-effect checks per refused case
            "ignored_only_env_present": "yes", "ignored_only_env_is_ignored": "yes",
            "status_before_phase_b_exit": "0", "phase_b_tracked": "1", "phase_b_untracked": "1",
            "phase_b_mentions_ignored": "0",
            # Phase B
            "override_ignore_uncommitted": OVERRIDE,
            "selected_ref": SELECTED_REF, "selected_ref_commit": SELECTED_SHA,
            "second_branch_commit": SECOND_SHA, "second_is_ancestor": "no",
            "bundle_create_exit": "0", "bundle_verify_exit": "0", "bundle_heads_exit": "0",
            "bundle_head_count": "1", "bundle_head_line": f"{SELECTED_SHA} {SELECTED_REF}",
            f"create_{SANDBOX}_exit": "0", "cp_exit": "0", f"probe_{SANDBOX}_exit": "0",
            f"image_{SANDBOX}": base,
            f"workspace_line_{SANDBOX}": "none · no workspace bind mount",
            f"rm_{SANDBOX}_exit": "0", "ls_after_exit": "0",
        }
        for case, (decision, exit_code, tracked, untracked) in MATRIX.items():
            self.obs.update({
                f"decide_{case}_exit": exit_code,
                f"decide_{case}_decision": decision,
                f"decide_{case}_tracked": tracked,
                f"decide_{case}_untracked": untracked,
                f"decide_{case}_bundle_exists": "no",
                f"decide_{case}_ls_exit": "0",
            })

    # --- helpers ---------------------------------------------------------------------------

    def write(self):
        for name, document in self.files.items():
            (self.work / name).write_text(json.dumps(document), encoding="utf-8")
        (self.work / f"probe-{SANDBOX}.env").write_text(
            "".join(f"{k}={v}\n" for k, v in self.probe.items()), encoding="utf-8"
        )
        obs_path = self.tmp / "observations.env"
        obs_path.write_text("".join(f"{k}={v}\n" for k, v in self.obs.items()), encoding="utf-8")
        return obs_path

    def results(self):
        status, criteria = self.record_module.record(
            str(self.write()), str(self.work), str(self.versions_path), str(self.evidence_path)
        )
        return status, {row["id"]: row["result"] for row in criteria}

    def assert_fails(self, criterion):
        status, results = self.results()
        self.assertEqual(status, "FAIL", results)
        self.assertEqual(results[criterion], "FAIL")

    # --- the baseline ---------------------------------------------------------------------------

    def test_both_phases_holding_passes(self):
        status, results = self.results()
        self.assertEqual(status, "PASS", results)
        self.assertTrue(all(r == "PASS" for r in results.values()), results)

    # --- Phase A ----------------------------------------------------------------------------------

    def test_the_decision_matrix_holds(self):
        # Every case must produce both the expected decision and its exit code.
        self.assertEqual(self.record_module.decision_failures(self.obs), [])
        status, results = self.results()
        self.assertEqual(results["G10.a1"], "PASS", results)
        self.assertEqual(status, "PASS")

    def test_each_refusing_case_must_refuse_with_exit_3(self):
        for case in REFUSED:
            for label, change in (
                ("allowed instead", {f"decide_{case}_decision": "allow", f"decide_{case}_exit": "0"}),
                ("refused but exit 0", {f"decide_{case}_exit": "0"}),
                ("refused with the wrong code", {f"decide_{case}_exit": "1"}),
                ("dirt not counted", {f"decide_{case}_tracked": "0", f"decide_{case}_untracked": "0"}),
            ):
                with self.subTest(case=case, broken=label):
                    self.setUp()
                    self.obs.update(change)
                    self.assertNotEqual(self.record_module.decision_failures(self.obs), [])
                    self.assert_fails("G10.a1")

    def test_each_allowing_case_must_allow_with_exit_0(self):
        for case in ("ignored_only", "clean"):
            for label, change in (
                ("refused instead", {f"decide_{case}_decision": "refuse", f"decide_{case}_exit": "3"}),
                ("allowed but non-zero exit", {f"decide_{case}_exit": "3"}),
                ("counted dirt", {f"decide_{case}_tracked": "1"}),
            ):
                with self.subTest(case=case, broken=label):
                    self.setUp()
                    self.obs.update(change)
                    self.assert_fails("G10.a1")

    def test_malformed_decision_evidence_fails_closed(self):
        for label, change in (
            ("exit not reported", {"decide_both_exit": ""}),
            ("decision not reported", {"decide_both_decision": ""}),
            ("counts not numbers", {"decide_both_tracked": "many"}),
            ("observation failure exit 2", {"decide_both_exit": "2"}),
        ):
            with self.subTest(case=label):
                self.setUp()
                self.obs.update(change)
                self.assert_fails("G10.a1")

    def test_the_ignored_only_control_must_not_refuse(self):
        for label, change in (
            ("control refused", {"decide_ignored_only_decision": "refuse", "decide_ignored_only_exit": "3"}),
            ("canary was not present", {"ignored_only_env_present": "no"}),
            ("canary is not actually ignored", {"ignored_only_env_is_ignored": "no"}),
        ):
            with self.subTest(case=label):
                self.setUp()
                self.obs.update(change)
                self.assert_fails("G10.a3")

    def test_a_bundle_or_sandbox_created_on_any_refused_path_fails(self):
        for case in REFUSED:
            with self.subTest(case=case, broken="bundle created"):
                self.setUp()
                self.obs[f"decide_{case}_bundle_exists"] = "yes"
                self.assert_fails("G10.a2")
            with self.subTest(case=case, broken="sandbox created"):
                self.setUp()
                self.files[f"ls-decide-{case}.json"] = {"sandboxes": [{"name": SANDBOX}]}
                self.assert_fails("G10.a2")
            with self.subTest(case=case, broken="listing unreadable"):
                self.setUp()
                self.files[f"ls-decide-{case}.json"] = {"unexpected": True}
                self.assert_fails("G10.a2")

    def test_phase_a_failure_blocks_phase_b(self):
        self.obs["decide_both_decision"] = "allow"
        self.obs["decide_both_exit"] = "0"
        status, results = self.results()
        self.assertEqual(status, "FAIL")
        self.assertEqual(results["G10.a1"], "FAIL")
        for later in ("G10.b1", "G10.b2"):
            self.assertEqual(results[later], "NOT-RUN")

    # --- Phase B preconditions: the override and the dirty tree it overrides ---------------------

    def test_phase_b_requires_the_explicit_override_marker(self):
        for label, change in (
            ("marker missing", {"override_ignore_uncommitted": None}),
            ("marker empty", {"override_ignore_uncommitted": ""}),
            ("marker reworded", {"override_ignore_uncommitted": "override"}),
        ):
            with self.subTest(case=label):
                self.setUp()
                if change["override_ignore_uncommitted"] is None:
                    del self.obs["override_ignore_uncommitted"]
                else:
                    self.obs.update(change)
                reasons = self.record_module.phase_b_precondition_failures(self.obs)
                self.assertTrue(any("override marker" in r for r in reasons), reasons)
                self.assert_fails("G10.b1")

    def test_phase_b_must_start_from_the_intended_dirty_tree(self):
        for label, change in (
            ("clean tree", {"phase_b_tracked": "0", "phase_b_untracked": "0"}),
            ("tracked dirt gone", {"phase_b_tracked": "0"}),
            ("untracked dirt gone", {"phase_b_untracked": "0"}),
            ("state not observed", {"status_before_phase_b_exit": "1"}),
            ("counts not numbers", {"phase_b_tracked": ""}),
            ("ignored file counted dirty", {"phase_b_mentions_ignored": "1"}),
        ):
            with self.subTest(case=label):
                self.setUp()
                self.obs.update(change)
                self.assertNotEqual(self.record_module.phase_b_precondition_failures(self.obs), [])
                self.assert_fails("G10.b1")

    # --- Phase B: bundle and ref ---------------------------------------------------------------------

    def test_bundle_must_export_exactly_the_selected_ref(self):
        for label, change in (
            ("verify failed", {"bundle_verify_exit": "1"}),
            ("create failed", {"bundle_create_exit": "1"}),
            ("list-heads failed", {"bundle_heads_exit": "1"}),
            ("two heads advertised", {"bundle_head_count": "2"}),
            ("head is a different commit", {"bundle_head_line": f"{'9' * 40} {SELECTED_REF}"}),
            ("head is a different ref", {"bundle_head_line": f"{SELECTED_SHA} refs/heads/other"}),
            ("ref is not the selected branch", {"selected_ref": "refs/heads/other"}),
            ("ref is raw HEAD", {"selected_ref": "HEAD"}),
        ):
            with self.subTest(case=label):
                self.setUp()
                self.obs.update(change)
                self.assert_fails("G10.b1")

    def test_the_advertised_head_is_matched_exactly(self):
        exact = f"{SELECTED_SHA} {SELECTED_REF}"
        self.assertEqual(self.record_module.advertised_head({"bundle_head_line": exact}),
                         (SELECTED_SHA, SELECTED_REF))
        for label, line in (
            ("suffix collision", f"{SELECTED_SHA} {SELECTED_REF}-evil"),
            ("prefix collision", f"{SELECTED_SHA} refs/heads/evil-{SELECTED_REF.rsplit('/', 1)[-1]}"),
            ("nested collision", f"{SELECTED_SHA} {SELECTED_REF}/inner"),
            ("extra field", f"{SELECTED_SHA} {SELECTED_REF} extra"),
            ("missing ref", SELECTED_SHA),
            ("missing sha", SELECTED_REF),
            ("short sha", f"{'1' * 12} {SELECTED_REF}"),
            ("non-hex sha", f"{'z' * 40} {SELECTED_REF}"),
            ("empty line", ""),
        ):
            with self.subTest(case=label):
                self.setUp()
                self.obs["bundle_head_line"] = line
                self.assert_fails("G10.b1")

    # --- Phase B: sandbox and delivery -----------------------------------------------------------------

    def test_wrong_template_or_a_mounted_workspace_fails(self):
        for label, change, files in (
            ("cached id is another image", {}, None),
            ("sbx resolved another base", {f"image_{SANDBOX}": "docker/sandbox-templates:other"}, {}),
            ("workspace bind mounted", {f"workspace_line_{SANDBOX}": "/Users/dev/repo · bind mount"}, {}),
            ("sbx cp failed", {"cp_exit": "1"}, {}),
            ("template store read failed", {"template_ls_exit": "1"}, {}),
        ):
            with self.subTest(case=label):
                self.setUp()
                if files is None:
                    self.files["templates.json"]["images"][0]["id"] = "deadbeef1234"
                else:
                    self.files.update(files)
                self.obs.update(change)
                self.assert_fails("G10.b2")

    def test_a_dirty_canary_in_the_vm_fails(self):
        for label, change in (
            ("ignored .env canary", {"env_canary_hits": "1"}),
            ("untracked canary", {"untracked_canary_hits": "1"}),
            ("uncommitted-edit canary", {"dirty_canary_hits": "1"}),
            (".env file delivered", {"env_file_present": "yes"}),
            ("untracked file delivered", {"untracked_file_present": "yes"}),
        ):
            with self.subTest(case=label):
                self.setUp()
                self.probe.update(change)
                self.assertNotEqual(self.record_module.delivery_failures(self.probe, self.obs), [])
                self.assert_fails("G10.b3")

    def test_a_host_workspace_mount_fails(self):
        for label, change in (
            ("/run/sandbox/source exists", {"source_mount_path_exists": "yes"}),
            ("a workspace mount is present", {"workspace_mounts": "1"}),
            ("mount count not reported", {"workspace_mounts": ""}),
        ):
            with self.subTest(case=label):
                self.setUp()
                self.probe.update(change)
                self.assert_fails("G10.b3")

    def test_the_committed_state_must_actually_arrive(self):
        for label, change in (
            ("baseline canary missing", {"baseline_canary_hits": "0"}),
            ("head is not the selected commit", {"head_commit": "9" * 40}),
            ("selected commit object missing", {"selected_commit_present": "no"}),
            ("clone failed", {"clone_exit": "1"}),
            ("run branch not created", {"checkout_exit": "1"}),
            ("probe incomplete", {"probe_complete": "no"}),
        ):
            with self.subTest(case=label):
                self.setUp()
                self.probe.update(change)
                self.assert_fails("G10.b3")

    # --- Phase B: second branch ---------------------------------------------------------------------------

    def test_any_trace_of_the_second_branch_fails(self):
        for label, change in (
            ("unique commit object present", {"second_commit_present": "yes"}),
            ("second ref present", {"second_ref_hits": "1"}),
            ("unique commit reachable", {"second_in_rev_list": "1"}),
            ("branch-only canary present", {"second_canary_hits": "1"}),
            ("branch-only file delivered", {"branch_only_file_present": "yes"}),
            ("commit presence not reported", {"second_commit_present": ""}),
        ):
            with self.subTest(case=label):
                self.setUp()
                self.probe.update(change)
                self.assertNotEqual(self.record_module.second_branch_failures(self.probe), [])
                self.assert_fails("G10.b4")

    def test_absence_is_proven_by_identity_not_by_rev_list_alone(self):
        # rev-list corroborates; the object check is what decides.
        self.probe.update({"second_commit_present": "yes", "second_in_rev_list": "0"})
        reasons = self.record_module.second_branch_failures(self.probe)
        self.assertTrue(any("cat-file -e" in r for r in reasons), reasons)

    # --- preflight, cleanup, evidence -------------------------------------------------------------------------

    def test_preflight_drift_blocks_the_gate(self):
        for label, files, obs in (
            ("a sandbox already exists", {"ls-baseline.json": {"sandboxes": [{"name": "other"}]}}, {}),
            ("ssh forwarding re-enabled",
             {"pf-ssh-forwarding.json": {"key": "ssh.agentForwardingEnabled", "value": True}}, {}),
            ("network baseline drifted", {"pf-policy.json": {"rules": []}}, {}),
            ("host agent socket not removed", {}, {"sbx_env_ssh_auth_sock": "present"}),
        ):
            with self.subTest(case=label):
                self.setUp()
                self.files.update(files)
                self.obs.update(obs)
                status, results = self.results()
                self.assertEqual(status, "FAIL")
                self.assertEqual(results["G10.a1"], "NOT-RUN")

    def test_cleanup_failure_fails(self):
        self.setUp()
        self.obs[f"rm_{SANDBOX}_exit"] = "1"
        self.assert_fails("G10.c1")
        # A run that stopped before Phase B never created the sandbox; rm failing on a missing
        # name is then not a cleanup failure, as long as no sandbox remains.
        self.setUp()
        del self.obs[f"create_{SANDBOX}_exit"]
        self.obs[f"rm_{SANDBOX}_exit"] = "1"
        status, results = self.results()
        self.assertEqual(results["G10.c1"], "PASS", results)
        self.setUp()
        del self.obs[f"create_{SANDBOX}_exit"]
        self.obs[f"rm_{SANDBOX}_exit"] = "1"
        self.files["ls-after.json"] = {"sandboxes": [{"name": SANDBOX}]}
        self.assert_fails("G10.c1")
        self.setUp()
        self.files["ls-after.json"] = {"sandboxes": [{"name": SANDBOX}]}
        self.assert_fails("G10.c1")

    def test_evidence_is_schema_valid_and_records_no_canary_value(self):
        self.results()
        evidence = json.loads(self.evidence_path.read_text(encoding="utf-8"))
        versions = json.loads(self.versions_path.read_text(encoding="utf-8"))
        self.assertEqual(
            [e.message for e in jsonschema.Draft202012Validator(self.evidence_schema).iter_errors(evidence)],
            [],
        )
        self.assertEqual(self.rules.evidence_problems(evidence, versions), [])
        text = json.dumps(evidence)
        # Canary IDs may appear; values never do. The fixture's values are 32 hex chars.
        self.assertNotRegex(text, r"\b[0-9a-f]{32}\b(?![0-9a-f])")
        for word in ("token", "secret", "password", "bearer", "cookie"):
            self.assertNotIn(word, text.lower())

    def test_stale_provenance_is_detected(self):
        self.results()
        evidence = json.loads(self.evidence_path.read_text(encoding="utf-8"))
        versions = json.loads(self.versions_path.read_text(encoding="utf-8"))
        drifted = json.loads(json.dumps(versions))
        drifted["sbx"]["exact"] = "v0.44.0"
        self.assertTrue(any("stale" in p for p in self.rules.evidence_problems(evidence, drifted)))

    def test_run_sh_keeps_the_gate_invariants(self):
        script = (GATES / "G10" / "run.sh").read_text(encoding="utf-8")
        body = "\n".join(line for line in script.splitlines() if not line.lstrip().startswith("#"))
        self.assertIn("env -u SSH_AUTH_SOCK sbx", body)
        self.assertNotIn("rm --all", body)
        self.assertNotIn("sbx reset", body)
        self.assertIn("--skills off", body)
        self.assertIn('bundle create', body)
        self.assertIn('refs/heads/$SELECTED', body)
        # Phase A's guard runs before anything in Phase B.
        self.assertLess(body.index("record.py --phase-a"), body.index("bundle create"))
        self.assertLess(body.index("record.py --phase-a"), body.index("sbx create claude"))
        stop = body.index("stop() {")
        stop_body = body[stop:body.index("}", body.index("record.py", stop))]
        self.assertLess(stop_body.index("cleanup"), stop_body.index("record.py"))


if __name__ == "__main__":
    unittest.main()
