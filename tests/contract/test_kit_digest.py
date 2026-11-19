"""The kit-source digest that binds PRODUCTION-CONFORMANCE to the kit it tested (tasks.md T114).

The point of these tests is the SYNC one: `kit_source_files()` is a hand-written enumeration, so
the only thing stopping it drifting from `stage()` is a test that watches a real staging run and
fails when `stage()` reads something the digest does not cover. Without it, the next file added to
the kit would silently fall outside the digest and T114's fail-open would be back.
"""

import hashlib
import importlib.util
import os
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[2]


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


stage_module = _load("dca_stage_digest", ROOT / "runtime" / "sandbox" / "kit" / "stage.py")

#: Files `stage()` reads that are deliberately NOT digest inputs, with the reason each is out.
EXPECTED_EXCLUSIONS = {
    # Selects which backends get staged; a digest recorded in it cannot depend on itself.
    "gates/eligibility.json",
}


class TestKitSourceFiles(unittest.TestCase):
    def test_every_entry_exists_and_is_committed_material(self):
        for relative, path in stage_module.kit_source_files():
            with self.subTest(file=relative):
                self.assertTrue(os.path.isfile(path), f"{relative} is not a file")
                self.assertFalse(relative.startswith("gates/"),
                                 f"{relative} is gate output, not kit source")
                self.assertNotIn("__pycache__", relative)

    def test_the_enumeration_covers_what_t114_named(self):
        relatives = {relative for relative, _ in stage_module.kit_source_files()}
        # The three T114 names outright, plus the T115 Node pin it says must be bound with them,
        # plus the rendering logic and templates that turn them into the kit.
        for required in ("src/dca/policy_gate.py",
                         "runtime/instructions/root.md",
                         "runtime/claude/managed-settings.json",
                         "runtime/sandbox/kit/node.json",
                         "runtime/sandbox/kit/stage.py",
                         "runtime/sandbox/kit/templates/dca-gate.sh.in"):
            self.assertIn(required, relatives)

    def test_paths_are_relative_posix_and_sorted(self):
        relatives = [relative for relative, _ in stage_module.kit_source_files()]
        self.assertEqual(relatives, sorted(relatives))
        for relative in relatives:
            self.assertFalse(relative.startswith("/"))
            self.assertNotIn("\\", relative)


class TestKitSourceDigest(unittest.TestCase):
    def test_the_digest_is_stable_and_shaped_like_the_others(self):
        first = stage_module.kit_source_digest()
        self.assertEqual(first, stage_module.kit_source_digest())
        self.assertTrue(first.startswith("sha256:"))
        self.assertEqual(len(first), len("sha256:") + 64)

    def test_one_changed_byte_in_any_input_changes_the_digest(self):
        before = stage_module.kit_source_digest()
        for relative, path in stage_module.kit_source_files():
            with self.subTest(file=relative):
                original = Path(path).read_bytes()
                try:
                    Path(path).write_bytes(original + b"\n# digest probe\n")
                    self.assertNotEqual(stage_module.kit_source_digest(), before,
                                        f"a change to {relative} did not move the digest")
                finally:
                    Path(path).write_bytes(original)
        self.assertEqual(stage_module.kit_source_digest(), before)

    def test_the_path_is_hashed_too_so_swapping_two_files_is_visible(self):
        """Hashing only the bytes would let two inputs trade places unnoticed."""
        files = dict(stage_module.kit_source_files())
        first, second = "runtime/instructions/researcher.md", "runtime/instructions/reviewer.md"
        before = stage_module.kit_source_digest()
        one, two = Path(files[first]).read_bytes(), Path(files[second]).read_bytes()
        try:
            Path(files[first]).write_bytes(two)
            Path(files[second]).write_bytes(one)
            self.assertNotEqual(stage_module.kit_source_digest(), before)
        finally:
            Path(files[first]).write_bytes(one)
            Path(files[second]).write_bytes(two)

    def test_a_missing_input_raises_rather_than_being_skipped(self):
        files = stage_module.kit_source_files()
        with mock.patch.object(stage_module, "kit_source_files",
                               return_value=files + [("runtime/absent", str(ROOT / "absent"))]):
            with self.assertRaises(OSError):
                stage_module.kit_source_digest()


class TestTheEnumerationCannotDriftFromStage(unittest.TestCase):
    """Watch a real `stage()` run and fail if it reads a repository file the digest misses."""

    def _staged_reads(self):
        covered = {path for _, path in stage_module.kit_source_files()}
        excluded = {str(ROOT / name) for name in EXPECTED_EXCLUSIONS}
        read = set()

        def note(path):
            try:
                resolved = os.path.realpath(str(path))
            except (TypeError, ValueError):
                return
            # Only repository files matter; the staging run writes under a temporary directory.
            if resolved.startswith(str(ROOT) + os.sep) and os.path.isfile(resolved):
                read.add(resolved)

        real_open, real_copyfile = open, shutil.copyfile

        def watched_open(file, mode="r", *args, **kwargs):
            if "w" not in str(mode) and "a" not in str(mode) and "x" not in str(mode):
                note(file)
            return real_open(file, mode, *args, **kwargs)

        def watched_copyfile(src, dst, **kwargs):
            note(src)
            return real_copyfile(src, dst, **kwargs)

        with tempfile.TemporaryDirectory(prefix="dca-kit-digest-") as temporary:
            artifact = Path(temporary) / "artifact"
            artifact.write_bytes(b"sentinel")
            with mock.patch.object(stage_module, "_stage_artifact", return_value=None), \
                    mock.patch("builtins.open", watched_open), \
                    mock.patch.object(shutil, "copyfile", watched_copyfile):
                stage_module.stage(Path(temporary) / "payload", backends=list(
                    stage_module.BACKENDS), skills_source=stage_module.SKILLS_SRC)
        return read, covered, excluded

    def test_stage_reads_nothing_outside_the_digest(self):
        read, covered, excluded = self._staged_reads()
        missed = sorted(os.path.relpath(path, ROOT) for path in read - covered - excluded)
        self.assertEqual(missed, [], "stage() reads these, but kit_source_digest() does not cover "
                                     "them; add them to kit_source_files() (or to this test's "
                                     "EXPECTED_EXCLUSIONS, with the reason)")

    def test_the_watcher_actually_observed_the_staging(self):
        """A watcher that silently saw nothing would make the test above vacuous."""
        read, _, _ = self._staged_reads()
        self.assertIn(str(ROOT / "runtime" / "instructions" / "root.md"),
                      {os.path.realpath(path) for path in read})
        self.assertGreater(len(read), 10)


if __name__ == "__main__":
    unittest.main()
