"""Launcher Phase 3C: retrieval, finalization and cleanup (tasks.md T070).

The inbound direction is the untrusted one: a bundle produced inside a sandbox that ran a model.
So these tests are mostly about the ORDER of the quarantine stages and about what must NOT have
happened when one of them fails.

The case that defines the whole stage is `test_22`: a returned bundle that PASSES
`git bundle verify` and still fails object-level validation. G5 observed it; `src/dca/source.py`
reproduces it; here it has to abort the run with exit 4, leave no `dca/<run-id>` ref, and leave no
object from that bundle in the developer's object store. An implementation that trusted
`git bundle verify` would import broken history and report success.

The other invariant is that an infrastructure failure is never dressed up as a task outcome: exit 4
writes NO completion report at all, keeps only `events.jsonl` and the gate log, and still removes
the sandbox.
"""

import importlib.util
import json
import os
import shutil
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
WORK = Path(__file__).resolve().parent / "work"
FIXTURES = ROOT / "tests" / "fixtures" / "eligibility"
FAKE_SBX = ROOT / "tests" / "fakes" / "sbx"


def _load(name, path):
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


errors = _load("dca_errors", ROOT / "src" / "dca" / "errors.py")
eligibility = _load("dca_eligibility", ROOT / "src" / "dca" / "eligibility.py")
events = _load("dca_events", ROOT / "src" / "dca" / "events.py")
launcher = _load("dca_launcher", ROOT / "src" / "dca" / "launcher.py")
report = _load("dca_report", ROOT / "src" / "dca" / "report.py")
sbx_module = _load("dca_sbx", ROOT / "src" / "dca" / "sbx.py")
g4 = _load("dca_g4_record", ROOT / "gates" / "G4" / "record.py")
rules = _load("dca_eligibility_rules", ROOT / "gates" / "eligibility_rules.py")


def git(repo, *args, check=True):
    proc = subprocess.run(["git", "-C", str(repo), *args], stdout=subprocess.PIPE,
                          stderr=subprocess.STDOUT, check=False)
    if check and proc.returncode != 0:
        raise AssertionError(f"git {' '.join(args)}: {proc.stdout.decode()}")
    return proc.returncode, proc.stdout.decode()


class FinalizationCase(unittest.TestCase):
    def setUp(self):
        WORK.mkdir(parents=True, exist_ok=True)
        self.dir = WORK / self.id().rsplit(".", 1)[-1]
        shutil.rmtree(self.dir, ignore_errors=True)
        self.dir.mkdir(parents=True)

        self.repo = self.dir / "repo"
        self.repo.mkdir()
        git(self.repo, "init", "--quiet", "--initial-branch=work")
        git(self.repo, "config", "user.email", "t@example.invalid")
        git(self.repo, "config", "user.name", "dca tests")
        (self.repo / "a.txt").write_text("alpha\n", encoding="utf-8")
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "--quiet", "-m", "initial")
        self.commit = git(self.repo, "rev-parse", "HEAD")[1].strip()

        self.versions = json.loads(
            (FIXTURES / "versions.synthetic.yaml").read_text(encoding="utf-8"))
        self.versions["sbx"] = {"exact": "v0.43.0", "minimum": "0.43.0"}
        self.versions_path = self.dir / "versions.yaml"
        self.versions_path.write_text(json.dumps(self.versions, indent=2, sort_keys=True),
                                      encoding="utf-8")
        self.document = json.loads((FIXTURES / "all-eligible.json").read_text(encoding="utf-8"))
        self.eligibility_path = self.dir / "eligibility.json"
        self.document["runtime_versions_digest"] = eligibility.canonical_digest(self.versions)
        self.document["pinned_versions"] = rules.pinned_versions(self.versions)
        self.document["network_policy_fingerprint"] = g4.fingerprint(
            {"rules": [], "governance": {"active": False}}, False)
        self.eligibility_path.write_text(json.dumps(self.document, indent=2, sort_keys=True),
                                         encoding="utf-8")

        self.state_dir = self.dir / "sbx"
        self.state_dir.mkdir()
        self.state = {
            "version": "sbx version: v0.43.0",
            "settings": {"ssh.agentForwardingEnabled": False, "ssh.agentSocketPath": ""},
            "policy": {"rules": [], "governance": {"active": False}},
            "sandboxes": [],
            "create_output": f"created from {self.versions['sandbox_bases']['claude']['base']}",
            "copy_out": {},
            "exec_output": {},
        }
        self.candidate_head = None
        self.write_state()
        self.sbx = sbx_module.Sbx(binary=str(FAKE_SBX),
                                  env={"DCA_FAKE_SBX_DIR": str(self.state_dir)})
        for name in launcher.PROVIDER_KEY_NAMES:
            os.environ.pop(name, None)

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def write_state(self):
        (self.state_dir / "state.json").write_text(json.dumps(self.state, indent=2),
                                                   encoding="utf-8")

    def stub_kit(self, path, backend):
        os.makedirs(path, exist_ok=True)
        Path(path, "spec.yaml").write_text(f"# stub {backend}\n", encoding="utf-8")
        return path

    def make(self, **overrides):
        options = {"repo": str(self.repo), "task": "fix the thing", "backend": "claude",
                   "trust": "trusted", "out": str(self.dir / "out")}
        options.update(overrides)
        request = launcher.RunRequest(**options)
        return launcher.Launcher(request, sbx=self.sbx, repo_root=str(ROOT),
                                 eligibility_path=str(self.eligibility_path),
                                 versions_path=str(self.versions_path),
                                 kit_builder=self.stub_kit)

    def candidate_bundle(self, run_id, mutate=None):
        """Build the bundle a real VM would return, from a clone that made a change."""
        vm = self.dir / f"vm-{run_id[-6:]}"
        subprocess.run(["git", "clone", "--quiet", str(self.repo), str(vm)], check=True,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        git(vm, "config", "user.email", "agent@example.invalid")
        git(vm, "config", "user.name", "agent")
        git(vm, "checkout", "--quiet", "-b", f"dca/{run_id}")
        (vm / "a.txt").write_text("patched\n", encoding="utf-8")
        (vm / "new.txt").write_text("added\n", encoding="utf-8")
        git(vm, "add", "-A")
        git(vm, "commit", "--quiet", "-m", "candidate")
        head = git(vm, "rev-parse", "HEAD")[1].strip()
        bundle = self.dir / f"result-{run_id[-6:]}.bundle"
        git(vm, "bundle", "create", str(bundle), f"refs/heads/dca/{run_id}")
        if mutate is not None:
            bundle.write_bytes(mutate(bytearray(bundle.read_bytes())))
        return bundle, head

    def prepared(self, mutate=None, head_override=None):
        """A provisioned launcher whose sandbox will hand back a candidate bundle."""
        instance = self.make()
        instance.preconditions()
        instance.provision()
        bundle, head = self.candidate_bundle(instance.request.run_id, mutate)
        self.candidate_head = head
        self.state["copy_out"]["/tmp/dca-result.bundle"] = str(bundle)
        self.state["exec_output"]["default"] = (head_override or head) + "\n"
        self.write_state()
        return instance


class TestRetrieval(FinalizationCase):
    def test_01_a_sound_candidate_is_imported_as_the_task_branch(self):
        instance = self.prepared()
        change_set = instance.retrieve()
        self.assertEqual(change_set["branch"], f"dca/{instance.request.run_id}")
        self.assertEqual(change_set["base_commit"], self.commit)
        self.assertEqual(change_set["head_commit"], self.candidate_head)
        self.assertEqual(sorted(item["path"] for item in change_set["files"]),
                         ["a.txt", "new.txt"])

    def test_02_the_change_set_is_computed_by_the_launcher_from_the_imported_history(self):
        instance = self.prepared()
        change_set = instance.retrieve()
        statuses = {item["path"]: item["status"] for item in change_set["files"]}
        self.assertEqual(statuses, {"a.txt": "modified", "new.txt": "added"})

    def test_03_the_developer_working_tree_is_never_touched(self):
        instance = self.prepared()
        before = (self.repo / "a.txt").read_text(encoding="utf-8")
        instance.retrieve()
        self.assertEqual((self.repo / "a.txt").read_text(encoding="utf-8"), before)
        self.assertEqual(git(self.repo, "rev-parse", "HEAD")[1].strip(), self.commit)

    def test_04_only_the_task_branch_ref_is_created(self):
        instance = self.prepared()
        before = set(git(self.repo, "for-each-ref", "--format=%(refname)")[1].split())
        instance.retrieve()
        after = set(git(self.repo, "for-each-ref", "--format=%(refname)")[1].split())
        self.assertEqual(after - before, {f"refs/heads/dca/{instance.request.run_id}"})

    def test_05_an_empty_change_set_creates_no_branch(self):
        instance = self.make()
        instance.preconditions()
        instance.provision()
        vm = self.dir / "vm-empty"
        subprocess.run(["git", "clone", "--quiet", str(self.repo), str(vm)], check=True,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        git(vm, "checkout", "--quiet", "-b", f"dca/{instance.request.run_id}")
        bundle = self.dir / "empty.bundle"
        git(vm, "bundle", "create", str(bundle), f"refs/heads/dca/{instance.request.run_id}")
        self.state["copy_out"]["/tmp/dca-result.bundle"] = str(bundle)
        self.state["exec_output"]["default"] = self.commit + "\n"
        self.write_state()
        change_set = instance.retrieve()
        self.assertIsNone(change_set["branch"])
        self.assertEqual(change_set["files"], [])
        code, _ = git(self.repo, "rev-parse", "--verify", "--quiet",
                      f"refs/heads/dca/{instance.request.run_id}", check=False)
        self.assertNotEqual(code, 0)


class TestRetrievalFailsClosed(FinalizationCase):
    def assert_aborts(self, instance):
        before = set(git(self.repo, "for-each-ref", "--format=%(refname)")[1].split())
        loose_before = sorted(str(p) for p in (self.repo / ".git" / "objects").rglob("*")
                              if p.is_file())
        with self.assertRaises(errors.InfraAbort) as caught:
            instance.retrieve()
        self.assertEqual(caught.exception.exit_code, 4)
        after = set(git(self.repo, "for-each-ref", "--format=%(refname)")[1].split())
        self.assertEqual(after, before, "no ref may be created by a failed retrieval")
        loose_after = sorted(str(p) for p in (self.repo / ".git" / "objects").rglob("*")
                             if p.is_file())
        self.assertEqual(loose_after, loose_before,
                         "no object from a rejected bundle may reach the object store")
        return caught.exception

    def test_20_a_truncated_returned_bundle_aborts_with_no_partial_ref(self):
        instance = self.prepared(mutate=lambda buffer: bytes(buffer[:-40]))
        self.assert_aborts(instance)

    def test_21_a_corrupted_returned_bundle_aborts(self):
        def flip(buffer):
            buffer[len(buffer) - 60] ^= 0xFF
            return bytes(buffer)

        self.assert_aborts(self.prepared(mutate=flip))

    def test_22_a_bundle_that_passes_header_verification_still_fails_object_validation(self):
        instance = self.prepared(mutate=lambda buffer: bytes(buffer[:-40]))
        bundle = Path(self.state["copy_out"]["/tmp/dca-result.bundle"])
        scratch = self.dir / "verify-scratch"
        subprocess.run(["git", "init", "--bare", "--quiet", str(scratch)], check=True)
        code, _ = git(scratch, "bundle", "verify", str(bundle), check=False)
        self.assertEqual(code, 0, "this test needs a bundle git bundle verify ACCEPTS")
        failure = self.assert_aborts(instance)
        self.assertIn("object-level", str(failure))

    def test_23_a_head_the_vm_did_not_report_aborts(self):
        instance = self.prepared(head_override="0" * 40)
        self.assert_aborts(instance)

    def test_24_an_export_failure_aborts(self):
        instance = self.make()
        instance.preconditions()
        instance.provision()
        self.state.setdefault("fail", {})["exec"] = 1
        self.write_state()
        with self.assertRaises(errors.InfraAbort) as caught:
            instance.retrieve()
        self.assertEqual(caught.exception.exit_code, 4)

    def test_25_a_copy_out_failure_aborts(self):
        instance = self.make()
        instance.preconditions()
        instance.provision()
        self.state["exec_output"]["default"] = self.commit + "\n"
        self.state["copy_out"] = {}
        self.write_state()
        with self.assertRaises(errors.InfraAbort):
            instance.retrieve()

    def test_26_an_abort_leaves_only_safe_artifacts(self):
        instance = self.prepared(mutate=lambda buffer: bytes(buffer[:-40]))
        os.makedirs(instance.request.out, exist_ok=True)
        Path(instance.request.out, "events.jsonl").write_text("", encoding="utf-8")
        with self.assertRaises(errors.InfraAbort):
            instance.retrieve()
        produced = sorted(p.name for p in Path(instance.request.out).iterdir())
        self.assertNotIn("report.json", produced)
        self.assertNotIn("report.md", produced)


class TestFinalVerificationAndReport(FinalizationCase):
    def test_40_the_launcher_reruns_every_required_check_itself(self):
        instance = self.make(verify=["make test", "make lint"])
        instance.preconditions()
        instance.provision()
        checks = instance.final_verification()
        self.assertEqual([check["id"] for check in checks], ["make test", "make lint"])
        for check in checks:
            self.assertEqual(check["executed_by"], "launcher")
            self.assertTrue(check["required"])
            self.assertTrue(check["after_last_change"])

    def test_41_a_failing_launcher_check_is_recorded_as_a_failure(self):
        self.state.setdefault("fail", {})["exec"] = 1
        self.write_state()
        instance = self.make(verify=["make test"])
        instance.preconditions()
        self.state["fail"] = {}
        self.write_state()
        instance.provision()
        self.state.setdefault("fail", {})["exec"] = 1
        self.write_state()
        checks = instance.final_verification()
        self.assertEqual(checks[0]["result"], "fail")
        self.assertEqual(checks[0]["exit_status"], 1)

    def test_42_check_output_is_kept_as_a_file_reference_not_inline(self):
        instance = self.make(verify=["make test"])
        instance.preconditions()
        instance.provision()
        checks = instance.final_verification()
        path = Path(instance.request.out) / checks[0]["output_ref"]
        self.assertTrue(path.is_file())

    def test_43_the_report_and_its_rendering_are_both_written_and_valid(self):
        instance = self.prepared()
        change_set = instance.retrieve()
        analysis = events.analyze(
            json.dumps({"type": "stream_started", "session_id": "s"}) + "\n"
            + json.dumps({"type": "stream_stopped", "session_id": "s", "reason": "normal"}) + "\n",
            exit_status=0)
        final = report.build(
            run_id=instance.request.run_id, backend="claude", trust_level="trusted",
            task_fingerprint=instance.request.task_fingerprint,
            source={"ref": instance.source_ref, "commit": instance.source_commit,
                    "bundle_sha256": instance.bundle_sha256, "uncommitted_ignored": False},
            sandbox_settings=instance.sandbox_settings, analysis=analysis, agent_report=None,
            change_set=change_set, limits_configured=instance.host_limits(),
            versions=instance.version_block())
        instance.write_outputs(final)
        out = Path(instance.request.out)
        self.assertTrue((out / "report.json").is_file())
        self.assertTrue((out / "report.md").is_file())
        report.validate(json.loads((out / "report.json").read_text(encoding="utf-8")))
        self.assertIn(instance.request.run_id, (out / "report.md").read_text(encoding="utf-8"))


class TestCleanup(FinalizationCase):
    def test_60_the_sandbox_is_removed_after_a_normal_finalization(self):
        instance = self.prepared()
        instance.retrieve()
        instance.cleanup()
        state = json.loads((self.state_dir / "state.json").read_text(encoding="utf-8"))
        self.assertIn(f"dca-{instance.request.run_id}", state.get("removed", []))
        self.assertEqual(state.get("sandboxes"), [])

    def test_61_the_sandbox_is_removed_after_a_retrieval_failure(self):
        instance = self.prepared(mutate=lambda buffer: bytes(buffer[:-40]))
        with self.assertRaises(errors.InfraAbort):
            instance.retrieve()
        instance.cleanup()
        state = json.loads((self.state_dir / "state.json").read_text(encoding="utf-8"))
        self.assertIn(f"dca-{instance.request.run_id}", state.get("removed", []))

    def test_62_cleanup_is_idempotent(self):
        instance = self.prepared()
        instance.cleanup()
        instance.cleanup()
        self.assertIsNone(instance.sandbox)

    def test_63_the_host_work_directory_is_removed(self):
        instance = self.prepared()
        workdir = instance.workdir
        instance.cleanup()
        self.assertFalse(os.path.exists(workdir))


if __name__ == "__main__":
    unittest.main()
