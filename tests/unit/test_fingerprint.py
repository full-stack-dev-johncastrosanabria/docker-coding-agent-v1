"""Unit tests for the workspace fingerprint (tasks.md T041, src/dca/fingerprint.py).

The fingerprint decides whether the candidate changed while the read-only reviewer read it, and
FR-022 makes that a safety-invariant violation. So the tests come in two halves that pull in
opposite directions on purpose: stability (the same tree must give the same digest, or the system
raises a violation on a run where nothing happened) and sensitivity (every kind of change a
reviewer could be misled by must move the digest).
"""

import tempfile
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
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


fingerprint = _load("dca_fingerprint", ROOT / "src" / "dca" / "fingerprint.py")


def git_ok(repo, *args):
    proc = subprocess.run(["git", "-C", str(repo), *args],
                          stdout=subprocess.PIPE, stderr=subprocess.STDOUT, check=False)
    if proc.returncode != 0:
        raise AssertionError(f"git {' '.join(args)}: {proc.stdout.decode()}")
    return proc.stdout.decode()


class FingerprintCase(unittest.TestCase):
    def setUp(self):
        WORK.mkdir(parents=True, exist_ok=True)
        self.dir = WORK / self.id().rsplit(".", 1)[-1]
        shutil.rmtree(self.dir, ignore_errors=True)
        self.repo = self.dir / "repo"
        self.repo.mkdir(parents=True)
        git_ok(self.repo, "init", "--quiet", "--initial-branch=work")
        git_ok(self.repo, "config", "user.email", "test@example.invalid")
        git_ok(self.repo, "config", "user.name", "dca tests")
        (self.repo / ".gitignore").write_text("*.ignored\nbuild/\n", encoding="utf-8")
        (self.repo / "a.txt").write_text("alpha\n", encoding="utf-8")
        (self.repo / "src").mkdir()
        (self.repo / "src" / "b.py").write_text("def f():\n    return 1\n", encoding="utf-8")
        git_ok(self.repo, "add", "-A")
        git_ok(self.repo, "commit", "--quiet", "-m", "initial")
        self.before = fingerprint.workspace_fingerprint(self.repo)

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def now(self):
        return fingerprint.workspace_fingerprint(self.repo)

    def assert_changed(self):
        self.assertNotEqual(self.now(), self.before)

    def assert_unchanged(self):
        self.assertEqual(self.now(), self.before)


class TestShape(FingerprintCase):
    def test_01_the_fingerprint_is_a_sha256_digest(self):
        self.assertRegex(self.before, r"^sha256:[0-9a-f]{64}$")

    def test_02_it_is_stable_across_repeated_computation(self):
        self.assertEqual([self.now() for _ in range(5)], [self.before] * 5)

    def test_03_it_covers_head_the_index_and_the_worktree(self):
        kinds = {line.split("\t", 1)[0] for line in fingerprint.fingerprint_lines(self.repo)}
        self.assertLessEqual({"head", "index", "work"}, kinds)

    def test_04_the_lines_are_sorted_so_directory_order_cannot_matter(self):
        lines = fingerprint.fingerprint_lines(self.repo)
        self.assertEqual(lines, sorted(lines))

    def test_05_a_non_repository_is_an_error_not_a_silent_digest(self):
        with self.assertRaises(RuntimeError):
            fingerprint.workspace_fingerprint(self.dir)


class TestSensitivity(FingerprintCase):
    def test_10_editing_a_tracked_file_changes_it(self):
        (self.repo / "a.txt").write_text("alpha changed\n", encoding="utf-8")
        self.assert_changed()

    def test_11_a_one_byte_edit_changes_it(self):
        (self.repo / "a.txt").write_text("alphb\n", encoding="utf-8")
        self.assert_changed()

    def test_12_a_new_untracked_file_changes_it(self):
        (self.repo / "new.txt").write_text("new\n", encoding="utf-8")
        self.assert_changed()

    def test_13_deleting_a_tracked_file_changes_it(self):
        os.remove(self.repo / "a.txt")
        self.assert_changed()

    def test_14_staging_a_change_changes_it_even_before_committing(self):
        (self.repo / "a.txt").write_text("staged\n", encoding="utf-8")
        after_edit = self.now()
        git_ok(self.repo, "add", "a.txt")
        self.assertNotEqual(self.now(), after_edit)
        self.assert_changed()

    def test_15_committing_changes_it_even_when_the_worktree_is_identical(self):
        (self.repo / "a.txt").write_text("committed\n", encoding="utf-8")
        git_ok(self.repo, "add", "-A")
        before_commit = self.now()
        git_ok(self.repo, "commit", "--quiet", "-m", "second")
        self.assertNotEqual(self.now(), before_commit)

    def test_16_a_mode_change_alone_changes_it(self):
        path = self.repo / "src" / "b.py"
        os.chmod(path, 0o755)
        self.assert_changed()

    def test_17_switching_branches_changes_it(self):
        git_ok(self.repo, "checkout", "--quiet", "-b", "other")
        self.assert_changed()

    def test_18_replacing_a_file_with_a_symlink_changes_it(self):
        path = self.repo / "a.txt"
        os.remove(path)
        os.symlink("src/b.py", path)
        self.assert_changed()

    def test_19_retargeting_a_symlink_changes_it(self):
        link = self.repo / "link"
        os.symlink("a.txt", link)
        git_ok(self.repo, "add", "-A")
        git_ok(self.repo, "commit", "--quiet", "-m", "link")
        self.before = self.now()
        os.remove(link)
        os.symlink("src/b.py", link)
        self.assert_changed()

    def test_20_a_new_empty_directory_with_a_file_changes_it(self):
        (self.repo / "pkg").mkdir()
        (self.repo / "pkg" / "c.py").write_text("", encoding="utf-8")
        self.assert_changed()

    def test_21_renaming_a_file_changes_it(self):
        git_ok(self.repo, "mv", "a.txt", "renamed.txt")
        self.assert_changed()


class TestStability(FingerprintCase):
    def test_30_ignored_files_never_move_it(self):
        (self.repo / "junk.ignored").write_text("noise\n", encoding="utf-8")
        (self.repo / "build").mkdir()
        (self.repo / "build" / "out.bin").write_bytes(b"\x00\x01")
        self.assert_unchanged()

    def test_31_rewriting_a_file_with_identical_content_does_not_move_it(self):
        (self.repo / "a.txt").write_text("alpha\n", encoding="utf-8")
        self.assert_unchanged()

    def test_32_reading_the_workspace_does_not_move_it(self):
        for path in self.repo.rglob("*"):
            if path.is_file():
                path.read_bytes()
        self.assert_unchanged()

    def test_33_a_change_and_its_exact_reversal_restores_the_fingerprint(self):
        (self.repo / "a.txt").write_text("temporarily different\n", encoding="utf-8")
        self.assert_changed()
        (self.repo / "a.txt").write_text("alpha\n", encoding="utf-8")
        self.assert_unchanged()

    def test_34_two_identical_repositories_agree(self):
        twin = self.dir / "twin"
        subprocess.run(["git", "clone", "--quiet", str(self.repo), str(twin)], check=True,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.assertEqual(
            [line for line in fingerprint.fingerprint_lines(twin) if line.startswith("work\t")],
            [line for line in fingerprint.fingerprint_lines(self.repo)
             if line.startswith("work\t")])


if __name__ == "__main__":
    unittest.main()


class TestReviewIdentity(unittest.TestCase):
    """`review_identity` is the host's FR-022 verdict on the managed hook record.

    It is deliberately strict in one direction only: equal fingerprints on both sides of the
    REVIEWER's delegation prove identity, and everything else - a mismatch, a hook that failed, one
    side missing, a pair that cannot be attributed to the reviewer - does not. Unproven is never
    reported as identical, and it is never reported as a mismatch either: "we cannot tell" and "it
    changed" are different findings and only the second is a safety violation.

    Event names are matched by generic token, so no harness's spelling is privileged. Both shipped
    spellings are exercised on every rule.
    """

    SAME = "sha256:" + "1" * 64
    OTHER = "sha256:" + "2" * 64
    SPELLINGS = (("SubagentStart", "SubagentStop"),      # Claude's managed hooks
                 ("on_agent_switch", "subagent_stop"))   # Codex's

    def record(self, event, agent, digest, error=None):
        return {"ts": "2026-09-26T00:00:00Z", "event": event, "agent": agent,
                "fingerprint": digest, "error": error}

    def pair(self, before, after, agent="reviewer", spelling=0, error=None):
        start, stop = self.SPELLINGS[spelling]
        return [self.record(start, agent, before, error),
                self.record(stop, agent, after, error)]

    def test_90_equal_fingerprints_around_the_reviewer_prove_identity(self):
        for spelling in (0, 1):
            with self.subTest(spelling=self.SPELLINGS[spelling]):
                verdict = fingerprint.review_identity(
                    self.pair(self.SAME, self.SAME, spelling=spelling))
                self.assertTrue(verdict["proven"])
                self.assertFalse(verdict["mismatch"])
                self.assertEqual(verdict["fingerprint_before"], self.SAME)
                self.assertEqual(verdict["fingerprint_after"], self.SAME)

    def test_91_differing_fingerprints_are_a_mismatch_not_merely_unproven(self):
        for spelling in (0, 1):
            with self.subTest(spelling=self.SPELLINGS[spelling]):
                verdict = fingerprint.review_identity(
                    self.pair(self.SAME, self.OTHER, spelling=spelling))
                self.assertFalse(verdict["proven"])
                self.assertTrue(verdict["mismatch"])

    def test_92_unproven_is_never_a_mismatch(self):
        for label, records in {
            "empty": [],
            "hook error": self.pair(None, None, error="OSError: boom"),
            "only a start": [self.pair(self.SAME, self.SAME)[0]],
            "only a stop": [self.pair(self.SAME, self.SAME)[1]],
            "not the reviewer": self.pair(self.SAME, self.SAME, agent="researcher"),
            "unlabelled": self.pair(self.SAME, self.SAME, agent="unknown"),
            "prose not a digest": self.pair("not recorded", "not recorded"),
            "bare prefix": self.pair("sha256:", "sha256:"),
            "unrecognised events": [self.record("whatever", "reviewer", self.SAME),
                                    self.record("whenever", "reviewer", self.SAME)],
        }.items():
            with self.subTest(case=label):
                verdict = fingerprint.review_identity(records)
                self.assertFalse(verdict["proven"], label)
                self.assertFalse(verdict["mismatch"], "unproven must not raise a safety event")
                self.assertTrue(verdict["reason"])

    def test_93_the_researchers_delegation_cannot_stand_in_for_the_reviewers(self):
        """The researcher is usually delegated BEFORE the edits, so its pair proves nothing here."""
        records = (self.pair(self.SAME, self.SAME, agent="researcher")
                   + self.pair(self.OTHER, self.OTHER, agent="reviewer"))
        verdict = fingerprint.review_identity(records)
        self.assertTrue(verdict["proven"])
        self.assertEqual(verdict["fingerprint_before"], self.OTHER,
                         "the reviewer's own pair is the one that is read")

    def test_94_the_widest_reviewer_window_is_used(self):
        """First start, last stop: a change anywhere inside the review is caught, never straddled."""
        records = [self.record("SubagentStart", "reviewer", self.SAME),
                   self.record("SubagentStop", "reviewer", self.SAME),
                   self.record("SubagentStop", "reviewer", self.OTHER)]
        verdict = fingerprint.review_identity(records)
        self.assertFalse(verdict["proven"])
        self.assertTrue(verdict["mismatch"])

    def test_95_a_record_of_any_shape_never_raises(self):
        for records in ([None], ["string"], [{}], [{"fingerprint": 5}], [{"event": None}]):
            with self.subTest(records=records):
                self.assertFalse(fingerprint.review_identity(records)["proven"])

    def test_96_reading_the_record_skips_bad_lines_and_never_raises(self):
        directory = tempfile.mkdtemp()
        try:
            path = os.path.join(directory, "fingerprints.jsonl")
            with open(path, "w", encoding="utf-8") as handle:
                handle.write('{"event": "SubagentStart", "agent": "reviewer", '
                             f'"fingerprint": "{self.SAME}"}}\n')
                handle.write("not json\n")
                handle.write("\n")
                handle.write("[1,2,3]\n")
                handle.write('{"event": "SubagentStop", "agent": "reviewer", '
                             f'"fingerprint": "{self.SAME}"}}\n')
            records = fingerprint.read_review_records(path)
            self.assertEqual(len(records), 2)
            self.assertTrue(fingerprint.review_identity(records)["proven"])
            self.assertEqual(fingerprint.read_review_records(
                os.path.join(directory, "absent.jsonl")), [])
        finally:
            shutil.rmtree(directory, ignore_errors=True)
