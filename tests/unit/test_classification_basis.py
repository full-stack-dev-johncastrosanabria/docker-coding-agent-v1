"""The pre-mutation scope and classification gate (tasks.md T083; FR-007, FR-001a).

Standard library only. This exists because of a measured failure, not a theory: on the 2026-09-26
campaign both backends classified a task `direct` and mutated the workspace before reading the test
that covers the function they were changing. That test imported a SECOND implementation and asserted a
rule across both, which is the coordinated-changes criterion - so the correct classification was
available before the first mutation, and the fixture's own reference solution reaches it "on the first
pass". Six earlier live runs did too. The agents then escalated, which FR-009 permits, but the plan
necessarily arrived after the mutation and the ordering rule failed them for it.

The remedy is upstream of the ordering rule: make `direct` a claim that has to be EARNED. It asserts
that no planned criterion applies, and not having noticed one is not evidence that none exists. So the
record carries a `classification_basis` naming each criterion, its verdict, and what was read.

Two layers, deliberately:

  * the in-VM gate (`policy_gate.basis_problem`) checks STRUCTURE and refuses mutations until the
    record is coherent. A refusal there is recoverable - the agent rewrites the record and continues,
    which is how the FR-001 ordering rule already gets obeyed;
  * the host (`bench.classification_basis_failures`) checks SUBSTANCE, where a thin answer is a scored
    failure. Putting substance in the gate would risk locking a run out over prose.

`direct` and `planned` are priced the same on purpose. A cheap `direct` invites asserting it without
looking; a cheap `planned` invites escaping the reading by over-classifying, which would fail the
direct fixtures just as surely.
"""

import importlib.util
import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from dca import bench  # noqa: E402


def load_gate():
    spec = importlib.util.spec_from_file_location(
        "dca_policy_gate_basis", ROOT / "src" / "dca" / "policy_gate.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


gate = load_gate()

CITED = "tests/test_ledger.py imports both formatters and asserts one rule"
TRACED = "the failing assertion names the padding branch directly"
NO_CALLERS = "billing/receipt.py is the only caller and passes positionally"


def basis(contract=False, coordinated=False, unclear=False, evidence=None):
    """A structurally complete basis. `evidence` overrides the coordinated-changes citation."""
    return {
        "contract_change": {"applies": contract, "evidence": [NO_CALLERS]},
        "coordinated_changes": {"applies": coordinated,
                                "evidence": [CITED if evidence is None else evidence]},
        "unclear_root_cause": {"applies": unclear, "evidence": [TRACED]},
    }


def record(value="direct", reason="one clear change in one file", tests=("tests/test_ledger.py",),
           **overrides):
    body = {
        "classification": {"value": value, "reason": reason},
        "classification_basis": basis(),
        "repository_map": {"scope": "minimal" if value == "direct" else "component",
                           "target_files": ["billing/amount.py"], "related_tests": list(tests)},
        "verification_approach": {"type": "deterministic", "checks": ["make test"]},
        "plan_ref": None if value == "direct" else "/run/dca/out/plan.md",
    }
    body.update(overrides)
    return body


class TestTheTwoValidatorsAgree(unittest.TestCase):
    """The gate refuses what the host would reject. Asserted, not left to a comment."""

    def test_01_the_criteria_and_threshold_are_identical(self):
        self.assertEqual(gate.CLASSIFICATION_CRITERIA, bench.CLASSIFICATION_CRITERIA)
        self.assertEqual(gate.MIN_EVIDENCE_CHARS, bench.MIN_EVIDENCE_CHARS)

    def test_02_both_reach_the_same_verdict_on_the_same_records(self):
        cases = [
            record(),
            record(value="planned", classification_basis=basis(coordinated=True)),
            record(classification_basis=basis(coordinated=True)),               # direct vs true
            record(value="planned"),                                            # planned vs all false
            {k: v for k, v in record().items() if k != "classification_basis"},  # absent
        ]
        for index, body in enumerate(cases):
            with self.subTest(case=index):
                value = (body.get("classification") or {}).get("value")
                gate_ok = gate.basis_problem(body, value) is None
                host_ok = bench._basis_problem(body, value) is None
                self.assertEqual(gate_ok, host_ok)


class TestStructureIsGated(unittest.TestCase):
    """What the in-VM gate refuses before the first mutation."""

    def problem(self, body):
        return gate._record_problem(body)

    def test_10_a_complete_direct_basis_is_accepted(self):
        self.assertIsNone(self.problem(record()))

    def test_11_a_complete_planned_basis_is_accepted(self):
        self.assertIsNone(self.problem(
            record(value="planned", classification_basis=basis(coordinated=True))))

    def test_12_a_missing_basis_is_refused(self):
        body = {k: v for k, v in record().items() if k != "classification_basis"}
        self.assertIn("no classification_basis", self.problem(body))

    def test_13_a_criterion_left_out_is_a_criterion_not_evaluated(self):
        for name in gate.CLASSIFICATION_CRITERIA:
            with self.subTest(missing=name):
                partial = {k: v for k, v in basis().items() if k != name}
                self.assertIn(name, self.problem(record(classification_basis=partial)))

    def test_14_applies_must_be_an_explicit_boolean(self):
        for bad in ("false", 0, None, "no"):
            with self.subTest(applies=bad):
                body = basis()
                body["coordinated_changes"]["applies"] = bad
                self.assertIn("explicit", self.problem(record(classification_basis=body)))

    def test_15_a_conclusion_is_not_evidence(self):
        for thin in ([], ["none"], ["n/a"], ["none apply"], [""], [None], ["  "]):
            with self.subTest(evidence=thin):
                body = basis()
                body["coordinated_changes"]["evidence"] = thin
                self.assertIsNotNone(self.problem(record(classification_basis=body)))

    def test_16_direct_contradicted_by_its_own_basis_is_refused(self):
        for kwargs in ({"contract": True}, {"coordinated": True}, {"unclear": True}):
            with self.subTest(**kwargs):
                problem = self.problem(record(classification_basis=basis(**kwargs)))
                self.assertIn("classifies direct while", problem)

    def test_17_planned_with_every_criterion_false_is_refused(self):
        problem = self.problem(record(value="planned"))
        self.assertIn("no planned criterion", problem)

    def test_18_the_gate_refuses_mutations_until_the_basis_is_coherent(self):
        """The gate is the pre-mutation enforcement point, not an after-the-fact audit."""
        directory = tempfile.mkdtemp()
        try:
            out = os.path.join(directory, "run", "dca", "out")
            os.makedirs(out)
            path = os.path.join(out, "context.json")
            saved = gate._RUN_DIR
            gate._RUN_DIR = os.path.join(directory, "run", "dca")
            try:
                bad = {k: v for k, v in record().items() if k != "classification_basis"}
                Path(path).write_text(json.dumps(bad), encoding="utf-8")
                self.assertIsNotNone(gate.context_record_problem())
                Path(path).write_text(json.dumps(record()), encoding="utf-8")
                self.assertIsNone(gate.context_record_problem())
            finally:
                gate._RUN_DIR = saved
        finally:
            shutil.rmtree(directory, ignore_errors=True)


class TestSubstanceIsScored(unittest.TestCase):
    """What the HOST rejects once the structure is sound. Scored, never a refusal."""

    def test_20_a_direct_claim_naming_no_related_tests_is_unbuilt(self):
        body = record(tests=())
        failures = bench.classification_basis_failures(body)
        self.assertTrue(any("names no related tests" in f for f in failures))

    def test_21_a_direct_claim_citing_nothing_in_the_repository_is_refused(self):
        for vague in ("nothing else needs to change here at all",
                      "no coordination is required for this change",
                      "I checked and found no other implementation"):
            with self.subTest(evidence=vague):
                body = record(classification_basis=basis(evidence=vague))
                failures = bench.classification_basis_failures(body)
                self.assertTrue(any("names no file" in f for f in failures), vague)

    def test_22_a_cited_path_satisfies_it_in_any_language(self):
        for cited in ("tests/test_ledger.py is the only importer",
                      "src/app.ts holds the only other caller",
                      "internal/ledger/format.go has no siblings",
                      "spec/models/amount_spec.rb covers it alone"):
            with self.subTest(evidence=cited):
                body = record(classification_basis=basis(evidence=cited))
                self.assertEqual(bench.classification_basis_failures(body), [])

    def test_23_planned_is_not_taxed(self):
        """The conservative answer must stay cheap, or runs are pushed toward the wrong one."""
        body = record(value="planned", tests=(),
                      classification_basis=basis(coordinated=True,
                                                 evidence="two implementations enforce one rule"))
        self.assertEqual(bench.classification_basis_failures(body), [])

    def test_24_a_truly_isolated_direct_change_stays_cheap(self):
        """No repository-wide exploration is required to earn `direct`."""
        body = record()
        self.assertIsNone(gate._record_problem(body))
        self.assertEqual(bench.classification_basis_failures(body), [])
        self.assertFalse((body["repository_map"].get("repo_wide_exploration") or {}).get("performed"),
                         "earning direct must not require a repository-wide scan")


class TestTheShippedTextIsGeneric(unittest.TestCase):
    """No fixture, provider or model may appear in anything an agent reads."""

    def shipped(self):
        instructions = ROOT / "runtime" / "instructions"
        skills = ROOT / "runtime" / "skills"
        return sorted(list(instructions.glob("*.md")) + list(skills.glob("*/SKILL.md")))

    def test_30_no_acceptance_fixture_is_named(self):
        import re
        fixture_id = re.compile(r"\b[KMR][0-9]\b")
        for path in self.shipped():
            with self.subTest(path=path.name):
                body = path.read_text(encoding="utf-8")
                self.assertIsNone(fixture_id.search(body))
                for name in ("format_usd", "format_eur", "test_books", "billing/usd",
                             "billing/eur", "TestLocaleReconciliation"):
                    self.assertNotIn(name, body, f"{name} is an acceptance-fixture symbol")

    def test_31_no_provider_or_model_is_named(self):
        import re
        forbidden = re.compile(r"claude|codex|anthropic|openai|chatgpt|gpt-|sonnet|opus|gemini|llama",
                               re.I)
        for path in self.shipped():
            with self.subTest(path=path.name):
                self.assertIsNone(forbidden.search(path.read_text(encoding="utf-8")))

    #: Each frozen thing is pinned at the commit that last CHANGED it with authorisation, not at
    #: `main`: these fixtures were created on this branch, so every one of them differs from main and a
    #: main comparison would prove nothing. `4392513` closed T082 and froze the fixtures; `63983a0`
    #: carries the oracle's last authorised change, the digest-extraction fix for a proven class-B
    #: defect. Move a pin only when a change to that artefact is itself deliberately authorised.
    FROZEN = {
        "4392513": ["benchmark/fixtures/M1", "benchmark/fixtures/M2", "benchmark/fixtures/M4",
                    "benchmark/fixtures/M6", "benchmark/thresholds.yaml"],
        "63983a0": ["benchmark/tools/planned_report.py"],
    }

    def test_32_the_frozen_fixtures_oracle_and_thresholds_are_untouched(self):
        """This remediation is product hardening. It may not buy a score by editing what scores it."""
        import subprocess
        for commit, paths in self.FROZEN.items():
            probe = subprocess.run(["git", "rev-parse", "--verify", f"{commit}^{{commit}}"],
                                   cwd=ROOT, capture_output=True, text=True)
            if probe.returncode != 0:
                self.skipTest(f"{commit} is not in this clone's history")
            with self.subTest(frozen_at=commit):
                changed = subprocess.run(["git", "diff", "--name-only", commit, "--", *paths],
                                         cwd=ROOT, capture_output=True, text=True).stdout.split()
                self.assertEqual(changed, [], f"a frozen artefact changed since {commit}")


if __name__ == "__main__":
    unittest.main()
