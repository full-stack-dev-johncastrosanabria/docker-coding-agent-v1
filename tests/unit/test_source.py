"""Unit tests for host-side source delivery and retrieval (tasks.md T037, src/dca/source.py).

These tests build real throwaway git repositories under `tests/unit/work/` rather than mocking
git, because every property here is a property of git's own behaviour. The single most important
one cannot be expressed against a mock at all: `git bundle verify` PASSES on a bundle whose
packfile has been truncated or corrupted, so an implementation that trusts it imports broken
history into the developer's repository. `test_30` reproduces that exact bundle - verify accepts
it, object-level validation must not - and an implementation that only runs `git bundle verify`
fails it.
"""

import importlib.util
import os
import shutil
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
WORK = Path(__file__).resolve().parent / "work"


def _load(name, path):
    """Load a stdlib-only module by path, ONCE per name.

    Registering it in `sys.modules` is what makes `assertRaises(errors.InfraAbort)` mean anything:
    `source.py` resolves its own error classes through the same cache, so the class the module
    raises is the class this file catches. Two loads would give two unrelated classes.
    """
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


errors = _load("dca_errors", ROOT / "src" / "dca" / "errors.py")
source = _load("dca_source", ROOT / "src" / "dca" / "source.py")


def git(repo, *args):
    proc = subprocess.run(["git", "-C", str(repo), *args],
                          stdout=subprocess.PIPE, stderr=subprocess.STDOUT, check=False)
    return proc.returncode, proc.stdout.decode("utf-8", "replace")


def git_ok(repo, *args):
    code, out = git(repo, *args)
    if code != 0:
        raise AssertionError(f"git {' '.join(args)} failed in {repo}: {out}")
    return out


class RepoCase(unittest.TestCase):
    """A fresh repository with three commits on `work`, plus a tag and a remote-tracking ref."""

    def setUp(self):
        WORK.mkdir(parents=True, exist_ok=True)
        self.dir = Path(WORK) / self.id().rsplit(".", 1)[-1]
        shutil.rmtree(self.dir, ignore_errors=True)
        self.dir.mkdir(parents=True)
        self.repo = self.dir / "repo"
        self.repo.mkdir()
        git_ok(self.repo, "init", "--quiet", "--initial-branch=work")
        git_ok(self.repo, "config", "user.email", "test@example.invalid")
        git_ok(self.repo, "config", "user.name", "dca tests")
        git_ok(self.repo, "config", "commit.gpgsign", "false")
        for n in range(1, 4):
            (self.repo / f"file{n}.txt").write_text(f"content {n}\n", encoding="utf-8")
            git_ok(self.repo, "add", "-A")
            git_ok(self.repo, "commit", "--quiet", "-m", f"commit {n}")
        self.commit = git_ok(self.repo, "rev-parse", "HEAD").strip()
        git_ok(self.repo, "tag", "v1")
        git_ok(self.repo, "update-ref", "refs/remotes/origin/work", self.commit)
        (self.repo / ".gitignore").write_text("ignored-dir/\n*.ignored\n", encoding="utf-8")
        git_ok(self.repo, "add", "-A")
        git_ok(self.repo, "commit", "--quiet", "-m", "add gitignore")
        self.commit = git_ok(self.repo, "rev-parse", "HEAD").strip()

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def refs(self):
        return sorted(git_ok(self.repo, "for-each-ref", "--format=%(refname) %(objectname)")
                      .splitlines())


# --- branch-ref validation ------------------------------------------------------------------------


class TestBranchRef(RepoCase):
    def test_01_a_local_branch_name_is_accepted(self):
        ref, commit = source.resolve_branch(self.repo, "work")
        self.assertEqual(ref, "refs/heads/work")
        self.assertEqual(commit, self.commit)

    def test_02_a_fully_qualified_head_ref_is_accepted(self):
        ref, commit = source.resolve_branch(self.repo, "refs/heads/work")
        self.assertEqual(ref, "refs/heads/work")
        self.assertEqual(commit, self.commit)

    def test_03_head_attached_to_a_local_branch_is_accepted(self):
        for requested in (None, "HEAD"):
            with self.subTest(requested=requested):
                ref, commit = source.resolve_branch(self.repo, requested)
                self.assertEqual(ref, "refs/heads/work")
                self.assertEqual(commit, self.commit)

    def test_04_a_second_branch_resolves_to_its_own_commit_not_head(self):
        git_ok(self.repo, "branch", "other", "HEAD~1")
        other = git_ok(self.repo, "rev-parse", "HEAD~1").strip()
        ref, commit = source.resolve_branch(self.repo, "other")
        self.assertEqual(ref, "refs/heads/other")
        self.assertEqual(commit, other)
        self.assertNotEqual(commit, self.commit)

    def test_05_a_tag_is_refused(self):
        for requested in ("v1", "refs/tags/v1"):
            with self.subTest(requested=requested):
                with self.assertRaises(errors.PreconditionError):
                    source.resolve_branch(self.repo, requested)

    def test_06_a_raw_commit_sha_is_refused(self):
        with self.assertRaises(errors.PreconditionError):
            source.resolve_branch(self.repo, self.commit)
        with self.assertRaises(errors.PreconditionError):
            source.resolve_branch(self.repo, self.commit[:10])

    def test_07_a_revision_expression_is_refused(self):
        for requested in ("HEAD~1", "work^", "work@{0}", "HEAD^{commit}"):
            with self.subTest(requested=requested):
                with self.assertRaises(errors.PreconditionError):
                    source.resolve_branch(self.repo, requested)

    def test_08_a_remote_tracking_ref_is_refused(self):
        for requested in ("origin/work", "refs/remotes/origin/work"):
            with self.subTest(requested=requested):
                with self.assertRaises(errors.PreconditionError):
                    source.resolve_branch(self.repo, requested)

    def test_09_an_ambiguous_name_is_refused_rather_than_resolved_by_precedence(self):
        git_ok(self.repo, "tag", "shared", self.commit)
        git_ok(self.repo, "branch", "shared", self.commit)
        with self.assertRaises(errors.PreconditionError) as caught:
            source.resolve_branch(self.repo, "shared")
        self.assertIn("ambiguous", str(caught.exception))

    def test_10_a_detached_head_is_refused(self):
        git_ok(self.repo, "checkout", "--quiet", "--detach", "HEAD")
        for requested in (None, "HEAD"):
            with self.subTest(requested=requested):
                with self.assertRaises(errors.PreconditionError):
                    source.resolve_branch(self.repo, requested)

    def test_11_a_missing_branch_is_refused(self):
        with self.assertRaises(errors.PreconditionError):
            source.resolve_branch(self.repo, "no-such-branch")

    def test_12_a_non_repository_is_refused(self):
        with self.assertRaises(errors.PreconditionError):
            source.resolve_branch(self.dir, "work")

    def test_13_every_refusal_carries_exit_status_3(self):
        with self.assertRaises(errors.PreconditionError) as caught:
            source.resolve_branch(self.repo, "refs/tags/v1")
        self.assertEqual(caught.exception.exit_code, 3)

    def test_14_ref_validation_creates_no_temporary_host_refs(self):
        before = self.refs()
        for requested in ("work", "refs/heads/work", "HEAD", "v1", self.commit, "HEAD~1",
                          "origin/work", "no-such-branch"):
            try:
                source.resolve_branch(self.repo, requested)
            except errors.PreconditionError:
                pass
        self.assertEqual(self.refs(), before)


# --- dirty checkout -------------------------------------------------------------------------------


class TestDirtyCheckout(RepoCase):
    def test_20_a_clean_checkout_passes_and_reports_nothing(self):
        self.assertEqual(source.check_clean(self.repo), [])

    def test_21_an_uncommitted_modification_is_refused(self):
        (self.repo / "file1.txt").write_text("changed\n", encoding="utf-8")
        with self.assertRaises(errors.PreconditionError) as caught:
            source.check_clean(self.repo)
        self.assertEqual(caught.exception.exit_code, 3)

    def test_22_an_untracked_non_ignored_file_is_refused(self):
        (self.repo / "new.txt").write_text("new\n", encoding="utf-8")
        with self.assertRaises(errors.PreconditionError):
            source.check_clean(self.repo)

    def test_23_a_staged_change_is_refused(self):
        (self.repo / "file2.txt").write_text("staged\n", encoding="utf-8")
        git_ok(self.repo, "add", "file2.txt")
        with self.assertRaises(errors.PreconditionError):
            source.check_clean(self.repo)

    def test_24_ignored_files_never_make_a_checkout_dirty(self):
        (self.repo / "build.ignored").write_text("artifact\n", encoding="utf-8")
        (self.repo / "ignored-dir").mkdir()
        (self.repo / "ignored-dir" / "x.txt").write_text("artifact\n", encoding="utf-8")
        self.assertEqual(source.check_clean(self.repo), [])

    def test_25_the_override_records_path_names_only(self):
        (self.repo / "file1.txt").write_text("changed\n", encoding="utf-8")
        (self.repo / "new.txt").write_text("secret value that must not be quoted\n",
                                           encoding="utf-8")
        dirty = source.check_clean(self.repo, ignore_uncommitted=True)
        self.assertEqual(dirty, ["file1.txt", "new.txt"])
        self.assertNotIn("secret value", " ".join(dirty))

    def test_26_a_rename_records_both_names(self):
        git_ok(self.repo, "mv", "file3.txt", "renamed.txt")
        dirty = source.check_clean(self.repo, ignore_uncommitted=True)
        self.assertEqual(dirty, ["file3.txt", "renamed.txt"])

    def test_27_a_deletion_is_reported(self):
        os.remove(self.repo / "file2.txt")
        dirty = source.check_clean(self.repo, ignore_uncommitted=True)
        self.assertEqual(dirty, ["file2.txt"])


# --- source bundle --------------------------------------------------------------------------------


class TestSourceBundle(RepoCase):
    def bundle(self):
        ref, commit = source.resolve_branch(self.repo, "work")
        path = self.dir / "source.bundle"
        digest = source.create_source_bundle(self.repo, ref, commit, path)
        return path, ref, commit, digest

    def test_40_the_bundle_is_created_from_the_branch_ref_and_verifies(self):
        path, ref, commit, digest = self.bundle()
        self.assertTrue(path.exists())
        self.assertRegex(digest, r"^[0-9a-f]{64}$")
        self.assertEqual(digest, source.file_sha256(path))
        git_ok(self.repo, "bundle", "verify", str(path))
        heads = git_ok(self.repo, "bundle", "list-heads", str(path)).split()
        self.assertEqual(heads, [commit, ref])

    def test_41_the_bundle_head_equals_the_branch_commit_not_head(self):
        git_ok(self.repo, "branch", "other", "HEAD~1")
        other = git_ok(self.repo, "rev-parse", "HEAD~1").strip()
        ref, commit = source.resolve_branch(self.repo, "other")
        path = self.dir / "other.bundle"
        source.create_source_bundle(self.repo, ref, commit, path)
        self.assertEqual(
            git_ok(self.repo, "bundle", "list-heads", str(path)).split(), [other, ref])

    def test_42_the_bundle_carries_committed_state_only(self):
        (self.repo / "uncommitted.txt").write_text("not delivered\n", encoding="utf-8")
        path, _, commit, _ = self.bundle()
        clone = self.dir / "clone"
        subprocess.run(["git", "clone", "--quiet", "--branch", "work", str(path), str(clone)],
                       check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.assertFalse((clone / "uncommitted.txt").exists())
        self.assertEqual(git_ok(clone, "rev-parse", "HEAD").strip(), commit)

    def test_43_creating_a_bundle_creates_no_temporary_host_refs(self):
        before = self.refs()
        self.bundle()
        self.assertEqual(self.refs(), before)

    def test_44_a_creation_failure_is_an_infrastructure_abort(self):
        ref, commit = source.resolve_branch(self.repo, "work")
        unwritable = self.dir / "nope"
        unwritable.write_text("not a directory\n", encoding="utf-8")
        with self.assertRaises(errors.InfraAbort) as caught:
            source.create_source_bundle(self.repo, ref, commit, unwritable / "x.bundle")
        self.assertEqual(caught.exception.exit_code, 4)

    def test_45_a_head_mismatch_is_an_infrastructure_abort(self):
        ref, _ = source.resolve_branch(self.repo, "work")
        with self.assertRaises(errors.InfraAbort):
            source.create_source_bundle(self.repo, ref, "0" * 40, self.dir / "mismatch.bundle")


# --- returned (untrusted) bundle ------------------------------------------------------------------


class RetrievalMixin:
    """Shared fixture: a VM clone that produced a candidate task branch and bundled it.

    A mixin rather than a base TestCase, so unittest collects each test exactly once instead of
    re-running the whole retrieval suite under every subclass that needs the same fixture.
    """

    RUN_ID = "run-2026-09-21T00-00-00Z-abc123"

    def setUp(self):
        super().setUp()
        self.task_ref = f"refs/heads/dca/{self.RUN_ID}"
        self.vm = self.dir / "vm"
        subprocess.run(["git", "clone", "--quiet", str(self.repo), str(self.vm)], check=True,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        git_ok(self.vm, "config", "user.email", "agent@example.invalid")
        git_ok(self.vm, "config", "user.name", "agent")
        git_ok(self.vm, "checkout", "--quiet", "-b", f"dca/{self.RUN_ID}")
        (self.vm / "file1.txt").write_text("patched\n", encoding="utf-8")
        (self.vm / "added.txt").write_text("added\n", encoding="utf-8")
        os.remove(self.vm / "file3.txt")
        git_ok(self.vm, "add", "-A")
        git_ok(self.vm, "commit", "--quiet", "-m", "candidate change")
        self.head = git_ok(self.vm, "rev-parse", "HEAD").strip()
        self.good = self.dir / "result.bundle"
        git_ok(self.vm, "bundle", "create", str(self.good), self.task_ref)

    def damaged(self, name, mutate):
        data = self.good.read_bytes()
        path = self.dir / name
        path.write_bytes(mutate(bytearray(data)))
        return path

    def assert_rejected(self, path):
        before = self.refs()
        with self.assertRaises(errors.InfraAbort) as caught:
            source.import_result_bundle(self.repo, path, self.RUN_ID, workdir=self.dir / "q")
        self.assertEqual(caught.exception.exit_code, 4)
        self.assertEqual(self.refs(), before, "a rejected bundle must leave the host repo untouched")
        code, _ = git(self.repo, "rev-parse", "--verify", "--quiet", self.task_ref)
        self.assertNotEqual(code, 0, "no dca/<run-id> ref may exist after a rejection")
        return caught.exception


class TestRetrieval(RetrievalMixin, RepoCase):
    def test_50_a_sound_bundle_is_validated_and_imported_as_the_task_branch(self):
        head = source.import_result_bundle(self.repo, self.good, self.RUN_ID,
                                           expected_commit=self.head, workdir=self.dir / "q")
        self.assertEqual(head, self.head)
        self.assertEqual(git_ok(self.repo, "rev-parse", self.task_ref).strip(), self.head)

    def test_51_importing_touches_no_other_ref_and_no_working_tree(self):
        before = set(self.refs())
        worktree = sorted(p.name for p in self.repo.iterdir() if p.name != ".git")
        source.import_result_bundle(self.repo, self.good, self.RUN_ID, workdir=self.dir / "q")
        after = set(self.refs())
        self.assertEqual({r.split()[0] for r in after} - {r.split()[0] for r in before},
                         {self.task_ref})
        self.assertEqual(sorted(p.name for p in self.repo.iterdir() if p.name != ".git"), worktree)
        self.assertEqual((self.repo / "file1.txt").read_text(encoding="utf-8"), "content 1\n")

    def test_52_the_launcher_computes_the_change_set_itself(self):
        source.import_result_bundle(self.repo, self.good, self.RUN_ID, workdir=self.dir / "q")
        files = source.change_set(self.repo, self.commit, self.head)
        self.assertEqual(files, [
            {"path": "added.txt", "status": "added"},
            {"path": "file1.txt", "status": "modified"},
            {"path": "file3.txt", "status": "deleted"},
        ])

    def test_53_a_truncated_bundle_is_rejected(self):
        self.assert_rejected(self.damaged("truncated.bundle", lambda b: bytes(b[:-40])))

    def test_54_a_byte_flipped_bundle_is_rejected(self):
        def flip(buffer):
            buffer[len(buffer) - 60] ^= 0xFF
            return bytes(buffer)

        self.assert_rejected(self.damaged("corrupt.bundle", flip))

    def test_55_an_empty_or_missing_bundle_is_rejected(self):
        empty = self.dir / "empty.bundle"
        empty.write_bytes(b"")
        self.assert_rejected(empty)
        self.assert_rejected(self.dir / "does-not-exist.bundle")

    def test_56_a_bundle_advertising_the_wrong_ref_is_rejected(self):
        wrong = self.dir / "wrong-ref.bundle"
        git_ok(self.vm, "bundle", "create", str(wrong), "refs/heads/work")
        self.assert_rejected(wrong)

    def test_57_a_bundle_with_several_heads_is_rejected(self):
        many = self.dir / "many.bundle"
        git_ok(self.vm, "bundle", "create", str(many), self.task_ref, "refs/heads/work")
        self.assert_rejected(many)

    def test_58_a_head_the_launcher_did_not_expect_is_rejected(self):
        before = self.refs()
        with self.assertRaises(errors.InfraAbort):
            source.import_result_bundle(self.repo, self.good, self.RUN_ID,
                                        expected_commit="0" * 40, workdir=self.dir / "q")
        self.assertEqual(self.refs(), before)

    def test_59_a_thin_bundle_that_needs_host_objects_is_rejected(self):
        thin = self.dir / "thin.bundle"
        git_ok(self.vm, "bundle", "create", str(thin), f"{self.commit}..{self.task_ref}")
        self.assert_rejected(thin)


class TestVerifyIsNotEnough(RetrievalMixin, RepoCase):
    """The regression that defines this module: `git bundle verify` accepts damaged bundles.

    G5 observed it; these tests pin it. Each damaged bundle below is asserted to PASS
    `git bundle verify`, so a `validate_result_bundle` implemented as "run git bundle verify" would
    accept it. The rejection therefore has to come from object-level validation, and it has to
    happen in the quarantine, before any host import.
    """

    def assert_verify_passes(self, path):
        scratch = self.dir / "verify-scratch"
        if not scratch.exists():
            subprocess.run(["git", "init", "--bare", "--quiet", str(scratch)], check=True)
        code, out = git(scratch, "bundle", "verify", str(path))
        self.assertEqual(code, 0,
                         f"this test needs a bundle git bundle verify ACCEPTS; git said: {out}")

    def test_30_a_truncated_bundle_passes_git_bundle_verify_and_must_still_be_rejected(self):
        path = self.damaged("verify-ok-truncated.bundle", lambda b: bytes(b[:-40]))
        self.assert_verify_passes(path)
        failure = self.assert_rejected(path)
        self.assertIn("object-level", str(failure))

    def test_31_a_corrupted_bundle_passes_git_bundle_verify_and_must_still_be_rejected(self):
        def flip(buffer):
            buffer[len(buffer) - 60] ^= 0xFF
            return bytes(buffer)

        path = self.damaged("verify-ok-corrupt.bundle", flip)
        self.assert_verify_passes(path)
        self.assert_rejected(path)

    def test_32_rejection_happens_before_any_write_to_the_developer_repository(self):
        path = self.damaged("pre-import.bundle", lambda b: bytes(b[:-40]))
        loose_before = sorted(str(p.relative_to(self.repo / ".git"))
                              for p in (self.repo / ".git").rglob("*") if p.is_file())
        self.assert_rejected(path)
        loose_after = sorted(str(p.relative_to(self.repo / ".git"))
                             for p in (self.repo / ".git").rglob("*") if p.is_file())
        self.assertEqual(
            [p for p in loose_after if p.startswith("objects/")],
            [p for p in loose_before if p.startswith("objects/")],
            "no object from a rejected bundle may reach the developer's object store")


if __name__ == "__main__":
    unittest.main()
