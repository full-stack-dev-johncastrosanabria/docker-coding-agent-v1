"""Unit tests for the workspace fingerprint (tasks.md T041, src/dca/fingerprint.py).

The fingerprint decides whether the candidate changed while the read-only reviewer read it, and
FR-022 makes that a safety-invariant violation. So the tests come in two halves that pull in
opposite directions on purpose: stability (the same tree must give the same digest, or the system
raises a violation on a run where nothing happened) and sensitivity (every kind of change a
reviewer could be misled by must move the digest).
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
