"""The kit-source digest that binds PRODUCTION-CONFORMANCE to the kit it tested (tasks.md T114).

The point of these tests is the SYNC one: `kit_source_files()` is a hand-written enumeration, so
the only thing stopping it drifting from `stage()` is a test that watches a real staging run and
fails when `stage()` reads something the digest does not cover. Without it, the next file added to
the kit would silently fall outside the digest and T114's fail-open would be back.
"""

import importlib.util
import json
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


rules = _load("dca_rules_kit_digest", ROOT / "gates" / "eligibility_rules.py")
launcher_eligibility = _load("dca_eligibility_kit_digest", ROOT / "src" / "dca" / "eligibility.py")
publish = _load("dca_publish_kit_digest", ROOT / "gates" / "production" / "publish.py")

OTHER_KIT = "sha256:" + "7" * 64


def _evidence(gate, kit=None):
    provenance = {"runtime_versions_digest": "sha256:" + "1" * 64}
    if kit is not None:
        provenance["kit_digest"] = kit
    return {"gate": gate, "status": "PASS", "provenance": provenance}


class TestOnlyProductionConformanceIsBound(unittest.TestCase):
    """The narrowness is the feature: a kit change must cost one re-run, not twenty."""

    def test_the_provenance_of_production_conformance_carries_the_kit_digest(self):
        versions = json.loads((ROOT / "runtime" / "versions.yaml").read_text(encoding="utf-8"))
        provenance = rules.evidence_provenance("PRODUCTION-CONFORMANCE", versions)
        self.assertEqual(provenance["kit_digest"], rules.kit_digest())
        self.assertIn("runtime_versions_digest", provenance)

    def test_no_other_gate_records_or_is_checked_for_a_kit_digest(self):
        versions = json.loads((ROOT / "runtime" / "versions.yaml").read_text(encoding="utf-8"))
        for gate in ("G0", "G4", "G6", "G9", "G11", "INVENTORY"):
            with self.subTest(gate=gate):
                self.assertNotIn("kit_digest", rules.evidence_provenance(gate, versions))
                # And a document without one is not stale for that reason.
                document = _evidence(gate)
                document["provenance"]["runtime_versions_digest"] = \
                    rules.runtime_versions_digest(versions)
                problems = [p for p in rules.evidence_problems(document, versions)
                            if "kit_digest" in p]
                self.assertEqual(problems, [])

    def test_a_changed_kit_stales_production_conformance_and_nothing_else(self):
        versions = json.loads((ROOT / "runtime" / "versions.yaml").read_text(encoding="utf-8"))
        current = rules.runtime_versions_digest(versions)
        documents = {}
        for gate in ("G0", "G4", "PRODUCTION-CONFORMANCE"):
            documents[gate] = _evidence(gate, kit=OTHER_KIT)
            documents[gate]["provenance"]["runtime_versions_digest"] = current
        documents["G0"]["provenance"] = {"pins": rules.evidence_provenance(
            "G0", versions)["pins"]}
        stale = {p.split(":")[0] for p in rules.check_evidence(documents, versions)}
        self.assertEqual(stale, {"PRODUCTION-CONFORMANCE"})

    def test_a_missing_kit_digest_is_stale_not_merely_absent(self):
        versions = json.loads((ROOT / "runtime" / "versions.yaml").read_text(encoding="utf-8"))
        document = _evidence("PRODUCTION-CONFORMANCE")
        document["provenance"]["runtime_versions_digest"] = \
            rules.runtime_versions_digest(versions)
        problems = rules.evidence_problems(document, versions)
        self.assertEqual(len(problems), 1)
        self.assertIn("kit_digest is missing", problems[0])

    def test_the_kit_is_a_parameter_so_the_rules_stay_a_function_of_their_inputs(self):
        """A synthetic environment binds against its own kit, not whatever is checked out."""
        versions = json.loads((ROOT / "runtime" / "versions.yaml").read_text(encoding="utf-8"))
        document = _evidence("PRODUCTION-CONFORMANCE", kit=OTHER_KIT)
        document["provenance"]["runtime_versions_digest"] = \
            rules.runtime_versions_digest(versions)
        self.assertEqual(rules.evidence_problems(document, versions, OTHER_KIT), [])
        self.assertNotEqual(rules.evidence_problems(document, versions), [])


class TestTheLauncherRefusesAfterAKitEdit(unittest.TestCase):
    """The half that makes T114 not fail-open: review-time checking alone is not enough."""

    def _document(self, kit):
        versions = launcher_eligibility.load_versions()
        return {"runtime_versions_digest": launcher_eligibility.canonical_digest(versions),
                "kit_digest": kit}, versions

    def test_a_matching_kit_passes_the_freshness_check(self):
        document, versions = self._document(launcher_eligibility.kit_digest())
        # Reaches the pinned_versions comparison rather than raising on the kit.
        try:
            launcher_eligibility.check_freshness(document, versions)
        except launcher_eligibility.EvidenceProblem as exc:
            self.assertNotIn("kit_digest", str(exc))

    def test_a_changed_kit_is_refused_with_the_remedy_named(self):
        document, versions = self._document(OTHER_KIT)
        with self.assertRaises(launcher_eligibility.EvidenceProblem) as caught:
            launcher_eligibility.check_freshness(document, versions)
        self.assertIn("kit_digest", str(caught.exception))
        self.assertIn("production conformance", str(caught.exception))

    def test_an_absent_kit_digest_is_refused_too(self):
        document, versions = self._document(None)
        del document["kit_digest"]
        with self.assertRaises(launcher_eligibility.EvidenceProblem) as caught:
            launcher_eligibility.check_freshness(document, versions)
        self.assertIn("kit_digest", str(caught.exception))

    def test_the_launcher_and_the_rules_agree_on_the_digest(self):
        self.assertEqual(launcher_eligibility.kit_digest(), rules.kit_digest())


class TestThePublishedKitIsTheTestedKit(unittest.TestCase):
    def test_cells_that_agree_with_the_sources_are_accepted(self):
        current = rules.kit_digest()
        foundation = {"cells": [{"backend": "claude", "profile": "trusted",
                                 "kit_digest": current},
                                {"backend": "codex", "profile": "trusted",
                                 "kit_digest": current}]}
        self.assertIsNone(publish.kit_digest_problem(foundation, current))

    def test_cells_that_staged_different_kits_are_refused(self):
        foundation = {"cells": [{"backend": "claude", "profile": "trusted", "kit_digest": OTHER_KIT},
                                {"backend": "codex", "profile": "trusted",
                                 "kit_digest": rules.kit_digest()}]}
        problem = publish.kit_digest_problem(foundation, rules.kit_digest())
        self.assertIn("different kits", problem)

    def test_a_kit_that_changed_since_the_run_is_refused(self):
        foundation = {"cells": [{"backend": "claude", "profile": "trusted",
                                 "kit_digest": OTHER_KIT}]}
        problem = publish.kit_digest_problem(foundation, rules.kit_digest())
        self.assertIn("re-run gates/production/run.py", problem)

    def test_cells_from_before_the_binding_are_refused_by_name(self):
        foundation = {"cells": [{"backend": "claude", "profile": "trusted"},
                                {"backend": "codex", "profile": "untrusted",
                                 "kit_digest": rules.kit_digest()}]}
        problem = publish.kit_digest_problem(foundation, rules.kit_digest())
        self.assertIn("claude/trusted", problem)
        self.assertIn("predate the T114 binding", problem)

    def test_an_empty_foundation_is_refused(self):
        self.assertIn("no cells", publish.kit_digest_problem({"cells": []}, rules.kit_digest()))
        self.assertIn("no cells", publish.kit_digest_problem(None, rules.kit_digest()))


if __name__ == "__main__":
    unittest.main()
