"""Contract tests for the gate review (tasks.md T024, gates/review.py).

Deterministic and offline: the committed evidence is copied into a temporary directory and mutated
there, so no test touches gates/eligibility.json, gates/SUMMARY.md or any accepted evidence.

T062 and T073 re-run this script, and its output is what declares a backend eligible, so the tests
concentrate on the ways that declaration could be wrong:

  * a flag authored rather than derived, so the document disagrees with its own validator;
  * a stale gate silently counted, or re-bound to new pins;
  * an unavailable backend carrying eligibility or a conformance status it cannot have;
  * the summary claiming an untrusted eligibility that no gate supports, or reading Claude's
    ambiguous G9 as the workload having reproduced authenticated capability.
"""

import importlib.util
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import jsonschema

ROOT = Path(__file__).resolve().parents[2]
GATES = ROOT / "gates"


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


review = _load("gate_review", GATES / "review.py")
rules = review.rules


class GateReview(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.schema = json.loads((GATES / "eligibility.schema.json").read_text(encoding="utf-8"))
        cls.versions = json.loads((ROOT / "runtime" / "versions.yaml").read_text(encoding="utf-8"))

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.gates = self.tmp / "gates"
        self.gates.mkdir()
        for evidence_id in rules.EVIDENCE_IDS:
            source = GATES / f"{evidence_id}.json"
            if source.exists():
                shutil.copy(source, self.gates / f"{evidence_id}.json")
        shutil.copy(GATES / "eligibility.schema.json", self.gates / "eligibility.schema.json")
        self.versions_path = self.tmp / "versions.yaml"
        shutil.copy(ROOT / "runtime" / "versions.yaml", self.versions_path)

    def _write(self, evidence_id, document):
        (self.gates / f"{evidence_id}.json").write_text(json.dumps(document), encoding="utf-8")

    def _evidence(self, evidence_id):
        return json.loads((self.gates / f"{evidence_id}.json").read_text(encoding="utf-8"))

    def _run(self):
        result = subprocess.run(
            [sys.executable, str(GATES / "review.py"), str(self.gates), str(self.versions_path)],
            capture_output=True, text=True)
        return result

    def _document(self):
        return json.loads((self.gates / "eligibility.json").read_text(encoding="utf-8"))

    # --- the clean path -----------------------------------------------------------------------

    def test_01_the_review_validates_against_the_schema_and_its_own_rules(self):
        result = self._run()
        self.assertEqual(result.returncode, 0, result.stderr)
        document = self._document()
        self.assertEqual(
            [e.message for e in
             jsonschema.Draft202012Validator(self.schema).iter_errors(document)], [])
        self.assertEqual(
            rules.check_review(document, rules.load_evidence(str(self.gates)), self.versions), [])

    def test_02_every_flag_is_derived_not_authored(self):
        """The document must agree with compute() for every backend and every flag."""
        self._run()
        document = self._document()
        for backend, expected in rules.compute(document).items():
            for flag, value in expected.items():
                with self.subTest(backend=backend, flag=flag):
                    self.assertIs(document["backends"][backend][flag], value)

    def test_03_both_backends_are_available_and_neither_is_untrusted_eligible(self):
        self._run()
        document = self._document()
        for backend in ("claude", "codex"):
            with self.subTest(backend=backend):
                self.assertTrue(document["backends"][backend]["available"])
                self.assertFalse(document["backends"][backend]["untrusted_eligible"])

    def test_04_trusted_eligible_requires_that_backend_s_production_conformance(self):
        """`trusted_eligible` means FINAL runtime readiness, and it is decided PER BACKEND.

        The rule, not the day's gate state: a backend whose production conformance is not PASS is
        never trusted-eligible, and a backend that is trusted-eligible has it PASS. Asserting a
        fixed NOT-RUN for both backends would make this test fail the moment T062 does its job.
        Mutating each backend's status in isolation exercises the refusal after both passed.
        """
        self._run()
        document = self._document()
        conformance = {backend: document["backends"][backend]["production_conformance"]
                       for backend in ("claude", "codex")}
        self.assertEqual(conformance, {"claude": "PASS", "codex": "PASS"})
        for backend, value in conformance.items():
            with self.subTest(backend=backend):
                denied = json.loads(json.dumps(document))
                denied["backends"][backend]["production_conformance"] = "NOT-RUN"
                self.assertFalse(rules.compute(denied)[backend]["trusted_eligible"])
                self.assertTrue(document["backends"][backend]["trusted_eligible"])

    def test_05_the_document_is_bound_to_the_current_pins(self):
        self._run()
        document = self._document()
        self.assertEqual(rules.check_binding(document, self.versions), [])
        self.assertEqual(document["runtime_versions_digest"],
                         rules.runtime_versions_digest(self.versions))

    def test_06_the_typed_backend_facts_come_from_evidence(self):
        self._run()
        document = self._document()
        self.assertEqual(document["backends"]["codex"]["credential_mechanism"],
                         self._evidence("G2")["codex_backend"]["credential_mechanism"])
        self.assertEqual(document["backends"]["claude"]["gate_status"]["G9"],
                         self._evidence("G9")["backends"]["claude"]["status"])
        self.assertEqual(document["network_policy_fingerprint"],
                         self._evidence("G4")["network_policy_fingerprint"])

    # --- stale evidence stops everything --------------------------------------------------------

    def test_07_stale_evidence_writes_nothing_and_exits_non_zero(self):
        """It must never count a stale gate as PASS, nor re-bind old evidence to new pins."""
        stale = self._evidence("G1a")
        stale["provenance"]["runtime_versions_digest"] = "sha256:" + "0" * 64
        self._write("G1a", stale)
        result = self._run()
        self.assertEqual(result.returncode, 1)
        self.assertFalse((self.gates / "eligibility.json").exists(),
                         "a stale review must write no eligibility document")
        self.assertFalse((self.gates / "SUMMARY.md").exists())
        self.assertIn("G1a", result.stderr)

    def test_08_a_stale_gate_is_never_turned_into_a_fail(self):
        stale = self._evidence("G3")
        stale["provenance"]["runtime_versions_digest"] = "sha256:" + "1" * 64
        self._write("G3", stale)
        result = self._run()
        self.assertEqual(result.returncode, 1)
        self.assertNotIn("FAIL", result.stderr)
        self.assertIn("re-run", result.stderr)

    def test_09_a_changed_pin_makes_every_gate_stale_and_stops_the_review(self):
        versions = json.loads(self.versions_path.read_text(encoding="utf-8"))
        versions["docker_agent"] = "v9.999.0"
        self.versions_path.write_text(json.dumps(versions), encoding="utf-8")
        result = self._run()
        self.assertEqual(result.returncode, 1)
        self.assertFalse((self.gates / "eligibility.json").exists())

    # --- an unavailable backend -----------------------------------------------------------------

    def test_10_an_unavailable_backend_is_recorded_explicitly(self):
        failed = self._evidence("G3")
        failed["status"] = "FAIL"
        self._write("G3", failed)
        result = self._run()
        self.assertEqual(result.returncode, 0, result.stderr)
        codex = self._document()["backends"]["codex"]
        self.assertFalse(codex["available"])
        self.assertTrue(codex["unavailable_reason"])
        self.assertFalse(codex["trusted_eligible"])
        self.assertFalse(codex["untrusted_eligible"])
        self.assertEqual(codex["production_conformance"], "NOT-APPLICABLE")
        self.assertEqual(
            [e.message for e in
             jsonschema.Draft202012Validator(self.schema).iter_errors(self._document())], [])

    def test_11_one_backend_being_unavailable_does_not_disturb_the_other(self):
        failed = self._evidence("G1a")
        failed["status"] = "FAIL"
        self._write("G1a", failed)
        self.assertEqual(self._run().returncode, 0)
        document = self._document()
        self.assertFalse(document["backends"]["claude"]["available"])
        self.assertTrue(document["backends"]["codex"]["available"])

    # --- the summary must not overclaim ----------------------------------------------------------

    def test_12_the_summary_never_claims_untrusted_eligibility(self):
        self._run()
        text = (self.gates / "SUMMARY.md").read_text(encoding="utf-8")
        self.assertIn("Untrusted execution stays blocked", text)
        self.assertIn("S5a", text)
        self.assertIn("split-plane", text)

    def test_13_the_summary_never_reads_claudes_g9_as_a_reproduced_capability(self):
        """Claude's G9 FAIL is inconclusive evidence, and the summary has to say so."""
        self._run()
        text = (self.gates / "SUMMARY.md").read_text(encoding="utf-8")
        self.assertIn("capability_reproduced=not_observed", text)
        self.assertIn("inconclusive", text)
        self.assertNotIn("capability_reproduced=observed", text)

    def test_14_the_summary_records_completed_g11_part_b(self):
        self._run()
        text = (self.gates / "SUMMARY.md").read_text(encoding="utf-8")
        evidence = self._evidence("G11")
        self.assertEqual(evidence["status"], "PASS")
        for backend in ("claude", "codex"):
            self.assertTrue(review._part_b_ran(evidence, backend))
        self.assertIn("criteria 5–8", text)

    def test_15_the_summary_records_the_phase_decision_and_the_codex_facts(self):
        self._run()
        text = (self.gates / "SUMMARY.md").read_text(encoding="utf-8")
        self.assertIn("runtime eligible on both backends", text)
        self.assertIn("token-file-trusted-only", text)
        self.assertIn("gpt-5.5", text)

    def test_16_no_credential_material_reaches_either_output(self):
        import re
        self._run()
        text = ((self.gates / "SUMMARY.md").read_text(encoding="utf-8")
                + (self.gates / "eligibility.json").read_text(encoding="utf-8"))
        for label, pattern in {
            "jwt": r"\beyJ[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}\.",
            "anthropic key": r"\bsk-ant-[a-z]{3}[0-9]{2}-",
            "openai key": r"\bsk-[A-Za-z0-9]{24,}",
            "bearer value": r"[Bb]earer\s+[A-Za-z0-9._\-]{12,}",
        }.items():
            with self.subTest(pattern=label):
                self.assertIsNone(re.search(pattern, text), label)

    def test_17_the_review_is_re_runnable_and_stable(self):
        """T062 and T073 re-run it; only the timestamp may differ."""
        self._run()
        first = self._document()
        self._run()
        second = self._document()
        first.pop("generated_at")
        second.pop("generated_at")
        self.assertEqual(first, second)


if __name__ == "__main__":
    unittest.main()
