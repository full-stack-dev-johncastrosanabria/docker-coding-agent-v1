"""Contract tests for the G5 recorder (tasks.md T013, gates/G5/record.py).

They run the decision logic against synthetic captures in a temporary work directory: no
sandbox, no sbx command, no git repository, no change to the real runtime/versions.yaml.

G5 is the return half of R11: a task commit leaves the VM as a bundle, is quarantined and
validated on the host, and is imported as exactly one ref while the host is otherwise
byte-identical. Both negatives — a corrupted returned bundle and a failed retrieval copy — must
import nothing and change nothing.
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
SANDBOX = "dca-g5-roundtrip"
RUN_ID = "g5-happy"
NEG_A = "g5-corrupt"
NEG_B = "g5-copyfail"
TASK_REF = f"refs/heads/dca/{RUN_ID}"
SOURCE_SHA = "a" * 40
CANDIDATE_SHA = "b" * 40
SELECTED_REF = "refs/heads/dca-g5-selected"


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class G5Recorder(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.record_module = _load("g5_record", GATES / "G5" / "record.py")
        cls.rules = _load("eligibility_rules", GATES / "eligibility_rules.py")
        cls.evidence_schema = json.loads((GATES / "evidence.schema.json").read_text(encoding="utf-8"))

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.work = self.tmp / "work"
        self.work.mkdir()
        self.evidence_path = self.tmp / "G5.json"
        self.versions_path = ROOT / "runtime" / "versions.yaml"
        pins = json.loads(self.versions_path.read_text(encoding="utf-8"))
        base = pins["sandbox_bases"]["claude"]["base"]
        digest = pins["sandbox_bases"]["claude"]["version"]
        repository, _, tag = base.rpartition(":")

        # Host snapshots: baseline, then post (one new ref), then unchanged for both negatives.
        self.snapshots = {
            "baseline": self.host_state(refs={SELECTED_REF: SOURCE_SHA}),
            "post": self.host_state(refs={SELECTED_REF: SOURCE_SHA, TASK_REF: CANDIDATE_SHA}),
            "after-negative-a": self.host_state(refs={SELECTED_REF: SOURCE_SHA, TASK_REF: CANDIDATE_SHA}),
            "after-negative-b": self.host_state(refs={SELECTED_REF: SOURCE_SHA, TASK_REF: CANDIDATE_SHA}),
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
        self.obs = {
            "sbx_env_ssh_auth_sock": "removed",
            "pf_version_exit": "0", "pf_ssh_forwarding_exit": "0", "pf_ssh_socket_exit": "0",
            "pf_policy_exit": "0", "pf_ls_exit": "0", "template_ls_exit": "0",
            "run_id": RUN_ID, "source_commit": SOURCE_SHA, "selected_ref": SELECTED_REF,
            "input_bundle_create_exit": "0", "input_bundle_verify_exit": "0",
            f"create_{SANDBOX}_exit": "0", "cp_in_exit": "0",
            f"image_{SANDBOX}": base,
            f"workspace_line_{SANDBOX}": "none · no workspace bind mount",
            # in-VM
            "vm_exit": "0", "vm_probe_complete": "yes", "vm_clone_exit": "0",
            "vm_hooks_path": "/dev/null", "vm_task_branch_exit": "0",
            "vm_task_branch_base": SOURCE_SHA, "vm_candidate_commit": CANDIDATE_SHA,
            "vm_candidate_ref": TASK_REF, "vm_task_bundle_exit": "0", "vm_task_bundle_heads": "1",
            # retrieval and validation
            "cp_out_exit": "0", "quarantine_inside_git": "no", "quarantine_bundle_bytes": "635",
            "returned_verify_exit": "0", "returned_heads_exit": "0", "returned_head_count": "1",
            "returned_head_line": f"{CANDIDATE_SHA} {TASK_REF}",
            "returned_scratch_fetch_exit": "0", "returned_scratch_ref": CANDIDATE_SHA,
            # import
            "candidate_ref_exists_before": "no", "fetch_attempted": "yes", "fetch_exit": "0",
            "candidate_ref_after": CANDIDATE_SHA,
            # negatives
            f"cp_out_{NEG_A}_exit": "0", f"{NEG_A}_bytes_before": "569", f"{NEG_A}_bytes_after": "189",
            f"{NEG_A}_verify_exit": "0", f"{NEG_A}_scratch_fetch_exit": "1", f"{NEG_A}_scratch_ref": "",
            f"{NEG_A}_fetch_attempted": "no", f"{NEG_A}_ref_after": "",
            f"{NEG_B}_vm_bundle": "present", f"cp_out_{NEG_B}_exit": "1",
            f"{NEG_B}_quarantine_exists": "no", f"{NEG_B}_fetch_attempted": "no",
            f"{NEG_B}_ref_after": "",
            f"rm_{SANDBOX}_exit": "0", "ls_after_exit": "0",
        }

    @staticmethod
    def host_state(refs, head=SOURCE_SHA, symref=SELECTED_REF, status="", index="idx", files="files"):
        return {
            "head": head + "\n",
            "symref": symref + "\n",
            "refs": "".join(f"{oid} {name}\n" for name, oid in sorted(refs.items())),
            "status": status,
            "index": index + "\n",
            "files": files + "\n",
        }

    # --- helpers ---------------------------------------------------------------------------

    def write(self):
        for name, document in self.files.items():
            (self.work / name).write_text(json.dumps(document), encoding="utf-8")
        for label, parts in self.snapshots.items():
            for part, text in parts.items():
                (self.work / f"snap-{label}-{part}.txt").write_text(text, encoding="utf-8")
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

    # --- the round trip ---------------------------------------------------------------------------

    def test_the_round_trip_passes(self):
        status, results = self.results()
        self.assertEqual(status, "PASS", results)
        self.assertTrue(all(r == "PASS" for r in results.values()), results)

    def test_the_task_branch_must_come_from_source_commit_with_hooks_disabled(self):
        for label, change in (
            ("branch started elsewhere", {"vm_task_branch_base": "c" * 40}),
            ("wrong task ref", {"vm_candidate_ref": "refs/heads/dca/other"}),
            ("nothing committed", {"vm_candidate_commit": SOURCE_SHA}),
            ("candidate not reported", {"vm_candidate_commit": ""}),
            ("hooks not disabled", {"vm_hooks_path": ""}),
            ("clone failed", {"vm_clone_exit": "1"}),
            ("branch not created", {"vm_task_branch_exit": "1"}),
            ("bundle creation failed", {"vm_task_bundle_exit": "1"}),
            ("bundle advertises several heads", {"vm_task_bundle_heads": "2"}),
            ("in-VM step incomplete", {"vm_probe_complete": "no"}),
        ):
            with self.subTest(case=label):
                self.setUp()
                self.obs.update(change)
                self.assert_fails("G5.2")

    # --- returned bundle identity and validation ------------------------------------------------------

    def test_the_returned_head_is_matched_exactly(self):
        exact = f"{CANDIDATE_SHA} {TASK_REF}"
        self.assertEqual(self.record_module.advertised_head({"returned_head_line": exact}),
                         (CANDIDATE_SHA, TASK_REF))
        for label, line in (
            ("wrong sha", f"{'9' * 40} {TASK_REF}"),
            ("suffix collision", f"{CANDIDATE_SHA} {TASK_REF}-evil"),
            ("prefix collision", f"{CANDIDATE_SHA} refs/heads/dca/evil-{RUN_ID}"),
            ("nested collision", f"{CANDIDATE_SHA} {TASK_REF}/inner"),
            ("extra field", f"{CANDIDATE_SHA} {TASK_REF} extra"),
            ("missing ref", CANDIDATE_SHA),
            ("missing sha", TASK_REF),
            ("short sha", f"{'b' * 12} {TASK_REF}"),
            ("malformed", "not a head line at all"),
            ("empty", ""),
        ):
            with self.subTest(case=label):
                self.setUp()
                self.obs["returned_head_line"] = line
                self.assert_fails("G5.3")

    def test_multiple_advertised_heads_fail(self):
        self.obs["returned_head_count"] = "2"
        self.assert_fails("G5.3")

    def test_validation_requires_more_than_bundle_verify(self):
        # `git bundle verify` passed on a truncated bundle in the real run, so the objects must
        # also unpack into the scratch quarantine repo.
        for label, change in (
            ("verify failed", {"returned_verify_exit": "1"}),
            ("scratch unpack failed", {"returned_scratch_fetch_exit": "1"}),
            ("scratch ref missing", {"returned_scratch_ref": ""}),
            ("scratch ref is another commit", {"returned_scratch_ref": "9" * 40}),
            ("list-heads failed", {"returned_heads_exit": "1"}),
            ("copy out failed", {"cp_out_exit": "1"}),
            ("empty quarantine bundle", {"quarantine_bundle_bytes": "0"}),
            ("quarantine inside .git", {"quarantine_inside_git": "yes"}),
        ):
            with self.subTest(case=label):
                self.setUp()
                self.obs.update(change)
                self.assertNotEqual(self.record_module.returned_bundle_failures(self.obs), [])
                self.assert_fails("G5.3")

    def test_the_import_guard_matches_the_validation(self):
        obs_path = str(self.write())
        self.assertEqual(self.record_module.main(["record.py", "--returned-ok", obs_path, str(self.work)]), 0)
        self.obs["returned_scratch_fetch_exit"] = "1"
        obs_path = str(self.write())
        self.assertEqual(self.record_module.main(["record.py", "--returned-ok", obs_path, str(self.work)]), 1)

    # --- import and host immutability --------------------------------------------------------------------

    def test_the_import_must_add_exactly_the_task_ref(self):
        for label, change in (
            ("ref pre-existed", {"candidate_ref_exists_before": "yes"}),
            ("import never ran", {"fetch_attempted": "no"}),
            ("fetch failed", {"fetch_exit": "1"}),
            ("ref absent afterwards", {"candidate_ref_after": ""}),
            ("ref points elsewhere", {"candidate_ref_after": "9" * 40}),
        ):
            with self.subTest(case=label):
                self.setUp()
                self.obs.update(change)
                self.assert_fails("G5.4")

    def test_any_host_change_beyond_the_new_ref_fails(self):
        for label, snapshot in (
            ("HEAD moved", self.host_state(refs={SELECTED_REF: SOURCE_SHA, TASK_REF: CANDIDATE_SHA},
                                           head=CANDIDATE_SHA)),
            ("branch switched", self.host_state(refs={SELECTED_REF: SOURCE_SHA, TASK_REF: CANDIDATE_SHA},
                                                symref=TASK_REF)),
            ("detached", self.host_state(refs={SELECTED_REF: SOURCE_SHA, TASK_REF: CANDIDATE_SHA},
                                         symref="DETACHED")),
            ("working tree changed", self.host_state(refs={SELECTED_REF: SOURCE_SHA, TASK_REF: CANDIDATE_SHA},
                                                     files="other")),
            ("index changed", self.host_state(refs={SELECTED_REF: SOURCE_SHA, TASK_REF: CANDIDATE_SHA},
                                              index="other")),
            ("status dirty", self.host_state(refs={SELECTED_REF: SOURCE_SHA, TASK_REF: CANDIDATE_SHA},
                                             status=" M tracked.txt\n")),
            ("pre-existing ref moved", self.host_state(refs={SELECTED_REF: CANDIDATE_SHA, TASK_REF: CANDIDATE_SHA})),
            ("pre-existing ref deleted", self.host_state(refs={TASK_REF: CANDIDATE_SHA})),
            ("unexpected extra ref", self.host_state(refs={SELECTED_REF: SOURCE_SHA, TASK_REF: CANDIDATE_SHA,
                                                           "refs/tags/v1": SOURCE_SHA})),
        ):
            with self.subTest(case=label):
                self.setUp()
                self.snapshots["post"] = snapshot
                self.assert_fails("G5.4")

    def test_a_missing_snapshot_fails_closed(self):
        self.setUp()
        del self.snapshots["post"]
        self.assert_fails("G5.4")

    # --- negatives -------------------------------------------------------------------------------------------

    def test_corrupted_bundle_negative(self):
        # It must be rejected by validation as a whole, import nothing and change nothing.
        status, results = self.results()
        self.assertEqual(results["G5.n1"], "PASS", results)
        for label, change, snapshot in (
            ("validation accepted it", {f"{NEG_A}_scratch_fetch_exit": "0",
                                        f"{NEG_A}_scratch_ref": CANDIDATE_SHA}, None),
            ("import ran anyway", {f"{NEG_A}_fetch_attempted": "yes"}, None),
            ("a ref appeared", {f"{NEG_A}_ref_after": CANDIDATE_SHA}, None),
            ("host changed", {}, self.host_state(refs={SELECTED_REF: SOURCE_SHA, TASK_REF: CANDIDATE_SHA},
                                                 head=CANDIDATE_SHA)),
        ):
            with self.subTest(case=label):
                self.setUp()
                self.obs.update(change)
                if snapshot is not None:
                    self.snapshots["after-negative-a"] = snapshot
                self.assert_fails("G5.n1")

    def test_copy_failure_negative(self):
        status, results = self.results()
        self.assertEqual(results["G5.n2"], "PASS", results)
        for label, change, snapshot in (
            ("the copy did not fail", {f"cp_out_{NEG_B}_exit": "0"}, None),
            ("a quarantine file was accepted", {f"{NEG_B}_quarantine_exists": "yes"}, None),
            ("import ran anyway", {f"{NEG_B}_fetch_attempted": "yes"}, None),
            ("a ref appeared", {f"{NEG_B}_ref_after": CANDIDATE_SHA}, None),
            ("the VM had no bundle, so nothing was proven", {f"{NEG_B}_vm_bundle": "missing"}, None),
            ("host changed", {}, self.host_state(refs={SELECTED_REF: SOURCE_SHA, TASK_REF: CANDIDATE_SHA},
                                                 files="other")),
        ):
            with self.subTest(case=label):
                self.setUp()
                self.obs.update(change)
                if snapshot is not None:
                    self.snapshots["after-negative-b"] = snapshot
                self.assert_fails("G5.n2")

    def test_the_negative_guards_only_open_on_success(self):
        obs_path = str(self.write())
        # Both guards exit non-zero for the observed (correct) failures, so no import runs.
        self.assertEqual(self.record_module.main(["record.py", "--negative-a-ok", obs_path, str(self.work)]), 1)
        self.assertEqual(self.record_module.main(["record.py", "--negative-b-ok", obs_path, str(self.work)]), 1)
        self.obs.update({f"{NEG_A}_verify_exit": "0", f"{NEG_A}_scratch_fetch_exit": "0",
                         f"cp_out_{NEG_B}_exit": "0"})
        obs_path = str(self.write())
        self.assertEqual(self.record_module.main(["record.py", "--negative-a-ok", obs_path, str(self.work)]), 0)
        self.assertEqual(self.record_module.main(["record.py", "--negative-b-ok", obs_path, str(self.work)]), 0)

    # --- preflight, cleanup, evidence ----------------------------------------------------------------------------

    def test_preflight_drift_blocks_the_gate(self):
        for label, files, obs in (
            ("a sandbox already exists", {"ls-baseline.json": {"sandboxes": [{"name": "other"}]}}, {}),
            ("ssh forwarding re-enabled",
             {"pf-ssh-forwarding.json": {"key": "ssh.agentForwardingEnabled", "value": True}}, {}),
            ("network baseline drifted", {"pf-policy.json": {"rules": []}}, {}),
            ("host agent socket not removed", {}, {"sbx_env_ssh_auth_sock": "present"}),
            ("version pin drifted", {"pf-version.json": {"client": {"version": "v0.44.0"},
                                                         "server": {"state": "running", "version": "v0.44.0"}}}, {}),
        ):
            with self.subTest(case=label):
                self.setUp()
                self.files.update(files)
                self.obs.update(obs)
                status, results = self.results()
                self.assertEqual(status, "FAIL")
                self.assertEqual(results["G5.1"], "NOT-RUN")

    def test_wrong_pinned_base_fails(self):
        for label, break_it in (
            ("cached id is another image",
             lambda: self.files["templates.json"]["images"][0].update({"id": "deadbeef1234"})),
            ("sbx resolved another base",
             lambda: self.obs.__setitem__(f"image_{SANDBOX}", "docker/sandbox-templates:other")),
            ("workspace bind mounted",
             lambda: self.obs.__setitem__(f"workspace_line_{SANDBOX}", "/Users/dev/repo · bind mount")),
            ("input bundle unverified", lambda: self.obs.__setitem__("input_bundle_verify_exit", "1")),
            ("copy into the VM failed", lambda: self.obs.__setitem__("cp_in_exit", "1")),
        ):
            with self.subTest(case=label):
                self.setUp()
                break_it()
                self.assert_fails("G5.1")

    def test_cleanup_failure_fails(self):
        self.setUp()
        self.obs[f"rm_{SANDBOX}_exit"] = "1"
        self.assert_fails("G5.c1")
        self.setUp()
        self.files["ls-after.json"] = {"sandboxes": [{"name": SANDBOX}]}
        self.assert_fails("G5.c1")

    def test_evidence_is_schema_valid_and_provenance_current(self):
        self.results()
        evidence = json.loads(self.evidence_path.read_text(encoding="utf-8"))
        versions = json.loads(self.versions_path.read_text(encoding="utf-8"))
        self.assertEqual(
            [e.message for e in jsonschema.Draft202012Validator(self.evidence_schema).iter_errors(evidence)],
            [],
        )
        self.assertEqual(self.rules.evidence_problems(evidence, versions), [])
        drifted = json.loads(json.dumps(versions))
        drifted["sbx"]["exact"] = "v0.44.0"
        self.assertTrue(any("stale" in p for p in self.rules.evidence_problems(evidence, drifted)))
        text = json.dumps(evidence).lower()
        for word in ("token", "secret", "password", "bearer", "cookie"):
            self.assertNotIn(word, text)

    def test_run_sh_keeps_the_gate_invariants(self):
        script = (GATES / "G5" / "run.sh").read_text(encoding="utf-8")
        body = "\n".join(line for line in script.splitlines() if not line.lstrip().startswith("#"))
        self.assertIn("env -u SSH_AUTH_SOCK sbx", body)
        self.assertNotIn("rm --all", body)
        self.assertNotIn("sbx reset", body)
        self.assertIn("--skills off", body)
        self.assertNotIn("git checkout", body.split("run_sbx exec")[0])  # no host checkout
        self.assertIn("--no-tags", body)
        # Validation happens before the *host* import. The scratch-repo fetch is part of
        # validation and legitimately runs earlier, so this anchors on the fixture fetch.
        host_fetch = 'git -C "$FIXTURE" fetch --no-tags'
        self.assertLess(body.index("record.py --returned-ok"), body.index(host_fetch))
        self.assertIn("git init -q --bare", body)  # the throwaway quarantine repo
        stop = body.index("stop() {")
        stop_body = body[stop:body.index("}", body.index("record.py", stop))]
        self.assertLess(stop_body.index("cleanup"), stop_body.index("record.py"))


if __name__ == "__main__":
    unittest.main()
