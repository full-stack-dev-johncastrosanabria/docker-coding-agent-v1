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


LOOKED = "searched for importers of the changed module and for a tests/ tree"


def discovery(result="found", performed=True, evidence=None):
    return {"performed": performed, "result": result,
            "evidence": [LOOKED if evidence is None else evidence]}


def record(value="direct", reason="one clear change in one file", tests=("tests/test_ledger.py",),
           test_discovery=None, **overrides):
    found = "found" if tests else "none"
    body = {
        "classification": {"value": value, "reason": reason},
        "classification_basis": basis(),
        "repository_map": {"scope": "minimal" if value == "direct" else "component",
                           "target_files": ["billing/amount.py"], "related_tests": list(tests),
                           "test_discovery": discovery(found) if test_discovery is None
                           else test_discovery},
        "verification_approach": {"type": "deterministic", "checks": ["make test"]},
        "plan_ref": None if value == "direct" else "/run/dca/out/plan.md",
    }
    body.update(overrides)
    return body


class FakeAnalysis:
    """Just enough of RunAnalysis for the corroboration checks: a record position and read history.

    Built from (order, agent, tool, paths) tuples so a test can say exactly who read what and when,
    which is the whole substance of what the host corroborates.
    """

    def __init__(self, context_record_at=10, reads=()):
        self.context_record_at = context_record_at
        self._reads = list(reads)

    def inspected_paths_before(self, order, agent="root"):
        return {path for at, who, _tool, paths in self._reads if at < order and who == agent
                for path in paths}

    def inspected_before(self, cited, order, agent="root"):
        from dca.events import same_path
        return any(same_path(cited, observed)
                   for observed in self.inspected_paths_before(order, agent))

    def looked_for_files_before(self, order, agent="root"):
        return any(at < order and who == agent for at, who, _tool, _paths in self._reads)


def read(order, path, agent="root"):
    return (order, agent, "read_file", [f"/workspace/{path}"])


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

    ANALYSIS = None  # set per test where corroboration matters

    def failures(self, body, analysis=None):
        return bench.classification_basis_failures(body, analysis)

    # --- test discovery accounting: "none found" is an answer, "never looked" is not -------------

    def searched(self, order=3):
        """A dispatched root search, which is what corroborates a "found none" claim."""
        return FakeAnalysis(context_record_at=10,
                            reads=[(order, "root", "grep", ["/workspace/tests"])])

    def test_19_a_found_none_claim_needs_a_search_to_have_happened(self):
        """An absence is the one claim no path can evidence, so the search itself is the evidence.

        Without this the `none` branch was the cheapest route through the whole gate - cheaper than
        looking - which reopened, for that branch alone, exactly the failure this exists to catch.
        """
        body = record(tests=(), test_discovery=discovery("none"))
        never_looked = FakeAnalysis(context_record_at=10, reads=[])
        failures = self.failures(body, never_looked)
        self.assertTrue(any("made no search or read of any kind" in f for f in failures))
        self.assertEqual(self.failures(body, self.searched()), [],
                         "a search before the record corroborates the claim")

    def test_19a_a_search_after_the_record_is_too_late(self):
        body = record(tests=(), test_discovery=discovery("none"))
        self.assertTrue(self.failures(body, self.searched(order=12)))

    def test_19b_another_agents_search_does_not_corroborate_roots_claim(self):
        body = record(tests=(), test_discovery=discovery("none"))
        analysis = FakeAnalysis(context_record_at=10,
                                reads=[(3, "researcher", "grep", ["/workspace/tests"])])
        self.assertTrue(self.failures(body, analysis))

    def test_20_no_tests_with_explicit_discovery_is_valid(self):
        """A repository may genuinely have no tests, and a task may forbid adding any.

        The earlier rule here demanded a non-empty `related_tests` for every direct classification.
        That was wrong and would have failed an accepted direct fixture whose task states outright
        that the repository has no automated tests and the change must not add any - the fixture was
        right and the rule was too strong.
        """
        body = record(tests=(), test_discovery=discovery("none"))
        self.assertEqual(self.failures(body), [])

    def test_20a_no_tests_and_no_discovery_accounting_is_refused(self):
        """The distinction that carries the weight: looked-and-found-none vs never-looked."""
        body = record(tests=())
        del body["repository_map"]["test_discovery"]
        failures = self.failures(body)
        self.assertTrue(any("does not account for test discovery" in f for f in failures))

    def test_20b_discovery_not_performed_is_refused(self):
        for performed in (False, None, "yes"):
            with self.subTest(performed=performed):
                body = record(tests=(), test_discovery=discovery("none", performed=performed))
                self.assertTrue(any("performed other than true" in f for f in self.failures(body)))

    def test_20c_an_unrecognised_discovery_result_is_refused(self):
        for result in ("maybe", "", None, "some"):
            with self.subTest(result=result):
                body = record(tests=(), test_discovery=discovery(result))
                self.assertTrue(any("test_discovery.result" in f for f in self.failures(body)))

    def test_20d_discovery_must_say_where_it_looked(self):
        for thin in ("none", "n/a", ""):
            with self.subTest(evidence=thin):
                body = record(tests=(), test_discovery=discovery("none", evidence=thin))
                self.assertTrue(any("cites nothing substantive" in f for f in self.failures(body)))

    def test_20e_discovery_and_related_tests_may_not_contradict(self):
        found_but_named_none = record(tests=(), test_discovery=discovery("found"))
        self.assertTrue(any("names none in related_tests" in f
                            for f in self.failures(found_but_named_none)))
        none_but_named_some = record(tests=("tests/test_a.py",), test_discovery=discovery("none"))
        self.assertTrue(any("contradict" in f for f in self.failures(none_but_named_some)))

    def test_20f_a_no_tests_repository_with_alternative_verification_is_valid(self):
        """The shape of the accepted direct fixture this rule must not break."""
        body = record(tests=(), test_discovery=discovery("none"),
                      verification_approach={"type": "alternative",
                                             "definition": "run the script and compare output",
                                             "limitation": "no automated suite exists"})
        self.assertEqual(self.failures(body), [])
        self.assertIsNone(gate._record_problem(body), "the gate must accept it too")

    # --- corroboration: a cited test must have been READ, by root, before the record -------------

    def test_25_a_named_test_read_by_root_before_the_record_is_corroborated(self):
        body = record(tests=("tests/test_ledger.py",))
        analysis = FakeAnalysis(context_record_at=10, reads=[read(4, "tests/test_ledger.py")])
        self.assertEqual(self.failures(body, analysis), [])

    def test_26_a_named_test_never_read_is_refused(self):
        body = record(tests=("tests/test_ledger.py",))
        analysis = FakeAnalysis(context_record_at=10, reads=[read(4, "billing/amount.py")])
        failures = self.failures(body, analysis)
        self.assertTrue(any("never read it before writing the Context Record" in f
                            for f in failures))

    def test_27_a_test_only_found_by_searching_is_not_evidence(self):
        """A grep proves the path exists. It cannot tell you what the test asserts."""
        analysis = FakeAnalysis(context_record_at=10,
                                reads=[(4, "root", "grep", ["/workspace/tests/test_ledger.py"])])
        # The fake models only reads, so a search contributes nothing - which is the real behaviour:
        # events.inspected_paths_before skips DISCOVER_TOOLS. Proven directly in test_events.
        analysis._reads = []
        body = record(tests=("tests/test_ledger.py",))
        self.assertTrue(self.failures(body, analysis))

    def test_28_a_test_read_after_the_record_is_too_late(self):
        body = record(tests=("tests/test_ledger.py",))
        analysis = FakeAnalysis(context_record_at=10, reads=[read(12, "tests/test_ledger.py")])
        self.assertTrue(self.failures(body, analysis))

    def test_29_a_read_by_the_researcher_or_reviewer_does_not_count(self):
        """Delegation must not launder root's obligation to look before it classifies."""
        for agent in ("researcher", "reviewer"):
            with self.subTest(agent=agent):
                body = record(tests=("tests/test_ledger.py",))
                analysis = FakeAnalysis(
                    context_record_at=10,
                    reads=[read(4, "tests/test_ledger.py", agent=agent)])
                self.assertTrue(self.failures(body, analysis))

    def test_29a_corroboration_is_skipped_when_there_is_nothing_to_corroborate_against(self):
        """No stream, or no record written, leaves the structural findings and adds none."""
        body = record(tests=("tests/test_ledger.py",))
        self.assertEqual(self.failures(body, None), [])
        self.assertEqual(self.failures(body, FakeAnalysis(context_record_at=None)), [])

    def test_29b_a_no_tests_record_needs_no_PATH_corroboration_but_still_needs_a_search(self):
        """There is no path to corroborate when the claim is that none exist - but the LOOKING is
        still corroborated. This test previously asserted the opposite and was wrong."""
        body = record(tests=(), test_discovery=discovery("none"))
        self.assertTrue(self.failures(body, FakeAnalysis(context_record_at=10, reads=[])),
                        "claiming none without looking is refused")
        looked = FakeAnalysis(context_record_at=10,
                              reads=[(3, "root", "grep", ["/workspace/tests"])])
        self.assertEqual(self.failures(body, looked), [],
                         "and no individual path is demanded, because none is claimed")

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


class TestInspectionIsReadNotSearch(unittest.TestCase):
    """`events.RunAnalysis.inspected_paths_before` on real streams, not a fake.

    This is the layer the corroboration rests on, so it is exercised against the genuine parser: a
    search must not count as a read, a refused call must not count at all, and another agent's reads
    must not count for root.
    """

    def stream(self, calls):
        """`calls` is (type, agent, tool, arguments) in order, written as the harness writes them."""
        directory = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, directory, True)
        path = os.path.join(directory, "events.jsonl")
        with open(path, "w", encoding="utf-8") as handle:
            for index, (kind, agent, tool, arguments) in enumerate(calls):
                handle.write(json.dumps({
                    "type": kind, "agent_name": agent,
                    "timestamp": f"2026-09-27T00:00:{index:02d}Z",
                    "tool_call": {"id": f"c{index}", "type": "function",
                                  "function": {"name": tool,
                                               "arguments": json.dumps(arguments)}}}) + "\n")
        from dca import events as E
        return E.analyze_file(path)

    def test_40_a_structured_read_counts(self):
        for tool in ("read_file", "Read", "view", "read"):
            with self.subTest(tool=tool):
                a = self.stream([("tool_call", "root", tool, {"path": "/workspace/tests/t.py"})])
                self.assertTrue(a.inspected_before("tests/t.py", 99))

    def test_41_a_search_does_not_count(self):
        for tool in ("grep", "Grep", "glob", "Glob", "search", "list_directory"):
            with self.subTest(tool=tool):
                a = self.stream([("tool_call", "root", tool, {"path": "/workspace/tests/t.py"})])
                self.assertFalse(a.inspected_before("tests/t.py", 99),
                                 f"{tool} proves the path exists, not that it was read")

    def test_42_a_shell_read_counts_but_a_shell_search_does_not(self):
        for command in ("cat tests/t.py", "head -20 tests/t.py", "nl tests/t.py",
                        "tail -5 tests/t.py"):
            with self.subTest(command=command):
                a = self.stream([("tool_call", "root", "shell", {"command": command})])
                self.assertTrue(a.inspected_before("tests/t.py", 99), command)
        for command in ("grep -rn foo tests/t.py", "find . -name t.py", "ls tests/"):
            with self.subTest(command=command):
                a = self.stream([("tool_call", "root", "shell", {"command": command})])
                self.assertFalse(a.inspected_before("tests/t.py", 99), command)

    def test_42a_never_credit_a_read_the_gate_would_have_refused(self):
        """The invariant that keeps the two lists from drifting apart.

        A review caught this: `sed`, `awk`, `less`, `more`, `od` and `strings` print files, so they were
        credited as reads - but none is in `shellparse.READ_ONLY_PROGRAMS`, because each can WRITE
        (`sed -i`, `awk > file`). `command_effect` therefore calls them `mutate` and the gate refuses
        them before a Context Record exists, which is exactly when the covering tests must be read.
        Crediting them promised a read path the gate blocks: the agent's genuine attempt would be
        denied and it would then be scored for not reading what it was prevented from reading. The set
        is now DERIVED from the gate's, so this cannot regress by hand-editing one list.
        """
        from dca import events as E, shellparse
        self.assertTrue(E.INSPECT_PROGRAMS <= shellparse.READ_ONLY_PROGRAMS)
        self.assertTrue(E.INSPECT_PROGRAMS, "the intersection must not be empty")
        for program in ("sed", "awk", "less", "more", "od", "strings"):
            with self.subTest(program=program):
                self.assertNotIn(program, E.INSPECT_PROGRAMS)
        for command in ("sed -n 1,40p tests/t.py", "less tests/t.py", "awk NR<40 tests/t.py"):
            with self.subTest(command=command):
                self.assertEqual(shellparse.command_effect(command)[0], "mutate",
                                 "if the gate now permits this, revisit INSPECT_PROGRAMS")
                a = self.stream([("tool_call", "root", "shell", {"command": command})])
                self.assertFalse(a.inspected_before("tests/t.py", 99))

    def test_47_looking_at_all_is_distinguishable_from_reading_a_file(self):
        """`looked_for_files_before` is the mirror of inspection: a search counts, nothing counts as
        nothing. It is what lets a "found none" claim be corroborated."""
        searched = self.stream([("tool_call", "root", "grep", {"path": "/workspace/tests"})])
        self.assertTrue(searched.looked_for_files_before(99))
        self.assertFalse(searched.inspected_before("tests/t.py", 99), "a grep is not a read")
        idle = self.stream([("tool_call", "root", "write_file", {"path": "/workspace/a.py"})])
        self.assertFalse(idle.looked_for_files_before(99))
        refused = self.stream([("hook_blocked", "root", "grep", {"path": "/workspace/tests"})])
        self.assertFalse(refused.looked_for_files_before(99), "a refused search found nothing")
        other = self.stream([("tool_call", "reviewer", "grep", {"path": "/workspace/tests"})])
        self.assertFalse(other.looked_for_files_before(99, agent="root"))

    def test_43_a_refused_call_read_nothing(self):
        a = self.stream([("hook_blocked", "root", "read_file", {"path": "/workspace/tests/t.py"})])
        self.assertFalse(a.inspected_before("tests/t.py", 99))

    def test_44_only_the_named_agents_reads_count(self):
        a = self.stream([("tool_call", "researcher", "read_file", {"path": "/workspace/tests/t.py"})])
        self.assertFalse(a.inspected_before("tests/t.py", 99, agent="root"))
        self.assertTrue(a.inspected_before("tests/t.py", 99, agent="researcher"))

    def test_45_order_is_respected(self):
        a = self.stream([("tool_call", "root", "read_file", {"path": "/workspace/tests/t.py"})])
        self.assertFalse(a.inspected_before("tests/t.py", 0))
        self.assertTrue(a.inspected_before("tests/t.py", 1))

    def test_46_scratch_reads_are_not_repository_evidence(self):
        a = self.stream([("tool_call", "root", "read_file",
                          {"path": "/run/dca/out/context.json"})])
        self.assertEqual(a.inspected_paths_before(99), set())


class TestTheScenarioThatFailed(unittest.TestCase):
    """The generic shape of the miss: a covering test that names a second implementation.

    Written without any fixture's symbols - a target function, a test importing two implementations
    and asserting one rule across them. Reading it is what makes the work planned; not reading it is
    what produced a wrong `direct`.
    """

    def test_50_reading_the_covering_test_supports_planned(self):
        body = record(value="planned", tests=("tests/test_rule.py",),
                      reason="one rule is enforced in two implementations",
                      classification_basis=basis(coordinated=True,
                                                 evidence="tests/test_rule.py imports both "
                                                          "surfaces and asserts one invariant"))
        self.assertIsNone(gate._record_problem(body))
        self.assertEqual(bench.classification_basis_failures(
            body, FakeAnalysis(context_record_at=9, reads=[read(3, "tests/test_rule.py")])), [])

    def test_51_claiming_direct_while_the_basis_says_coordinated_is_refused_by_the_gate(self):
        body = record(classification_basis=basis(coordinated=True))
        self.assertIn("classifies direct while", gate._record_problem(body))

    def test_52_claiming_direct_without_reading_the_covering_test_is_scored_a_failure(self):
        body = record(tests=("tests/test_rule.py",))
        analysis = FakeAnalysis(context_record_at=9, reads=[read(3, "src/surface_one.py")])
        self.assertTrue(bench.classification_basis_failures(body, analysis))


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
