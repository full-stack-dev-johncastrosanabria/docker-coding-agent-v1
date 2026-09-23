"""`dca bench` (tasks.md T075/T076), with no model, no sandbox and no network.

The benchmark's value is that it measures the real product, so the property tested hardest here is
the one that is easiest to lose: every fixture run goes through the `dca run` process boundary
(here, `tests/fakes/dca_run`, which writes a report with the real `dca.report` module), and the
score is derived only from what that run left behind - never from anything the benchmark assumed.

The scoring matrix is exhaustive on purpose. Each way a run can fail to count as a pass - wrong
outcome, no report, an invalid report, an oracle failure, an out-of-scope change, a leaked
sandbox - is its own test, and `blocked` is kept distinct from `failed` throughout.
"""

import argparse
import contextlib
import importlib.util
import io
import json
import os
import re
import shutil
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
WORK = Path(__file__).resolve().parent / "work" / "bench"
FAKE_SBX = ROOT / "tests" / "fakes" / "sbx"
FAKE_DCA = ROOT / "tests" / "fakes" / "dca_run"
ELIGIBILITY = ROOT / "tests" / "fixtures" / "eligibility"


def _load(name, path):
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


errors = _load("dca_errors", ROOT / "src" / "dca" / "errors.py")
bench = _load("dca_bench", ROOT / "src" / "dca" / "bench.py")
sbx_module = _load("dca_sbx", ROOT / "src" / "dca" / "sbx.py")

EXPECTED_IDS = ["R1", "R2", "R3", "R4", "R5", "R6", "R7", "R8", "R9", "R10"]


def record(**overrides):
    """A run record that passes, before the override under test."""
    base = {"exit_status": 0, "final_outcome": "succeeded", "report_schema_valid": True,
            "oracle": "pass", "out_of_scope": [], "cleanup": "ok", "leaked_sandboxes": []}
    base.update(overrides)
    return base


FIXTURE = {"expected_disposition": "succeeded"}


class TestFixtureSuite(unittest.TestCase):
    """The committed suite: schema-valid, deterministic, and the shape the benchmark promises."""

    def test_01_every_fixture_is_discovered_in_natural_order_and_is_schema_valid(self):
        fixtures = bench.discover()
        self.assertEqual([f["id"] for f in fixtures], EXPECTED_IDS)

    def test_02_small_fixtures_are_direct_and_medium_fixtures_are_planned(self):
        for fixture in bench.discover():
            with self.subTest(fixture=fixture["id"]):
                expected = "direct" if fixture["category"] == "small" else "planned"
                self.assertEqual(fixture["expected_classification"], expected)
                self.assertEqual(fixture["trust_level"], "both")
                self.assertEqual(fixture["expected_disposition"], "succeeded")

    def test_03_every_fixture_has_hidden_evidence_and_deterministic_verification(self):
        for fixture in bench.discover():
            with self.subTest(fixture=fixture["id"]):
                self.assertEqual(fixture["verification"]["type"], "deterministic")
                self.assertTrue(fixture["verification"]["commands"])
                directory = Path(fixture["_dir"])
                hidden = list((directory / "hidden").glob("*")) + list(
                    (directory / "mutants").glob("*"))
                self.assertTrue(hidden, "the oracle must check more than the agent could see")
                self.assertFalse((directory / "seed" / "hidden").exists())
                self.assertTrue(fixture["golden"]["good"])
                self.assertGreaterEqual(len(fixture["golden"]["bad"]), 2)

    def test_04_every_seed_ignores_bytecode_so_a_verification_run_is_not_a_change(self):
        for fixture in bench.discover():
            with self.subTest(fixture=fixture["id"]):
                ignore = (Path(fixture["_dir"]) / "seed" / ".gitignore").read_text()
                self.assertIn("__pycache__/", ignore)

    def test_05_selection_is_by_comma_separated_id_globs(self):
        self.assertEqual([f["id"] for f in bench.discover(selection="R[1-8]")], EXPECTED_IDS[:8])
        self.assertEqual([f["id"] for f in bench.discover(selection="R1, R10")], ["R1", "R10"])
        self.assertEqual([f["id"] for f in bench.discover(selection="R1*")], ["R1", "R10"])
        self.assertEqual(bench.discover(selection="Z*"), [])

    def test_06_a_fixture_that_breaks_the_schema_is_rejected(self):
        directory = WORK / "R11"
        shutil.rmtree(directory, ignore_errors=True)
        shutil.copytree(ROOT / "benchmark" / "fixtures" / "R1", directory)
        (directory / "fixture.yaml").write_text(
            (directory / "fixture.yaml").read_text().replace('"R1"', '"R11"'))
        with self.assertRaisesRegex(ValueError, "fixture.schema.json"):
            bench.load_fixture(str(directory))

    def test_07_a_fixture_whose_id_is_not_its_directory_is_rejected(self):
        directory = WORK / "R2"
        shutil.rmtree(directory, ignore_errors=True)
        shutil.copytree(ROOT / "benchmark" / "fixtures" / "R1", directory)
        with self.assertRaisesRegex(ValueError, "does not match its directory"):
            bench.load_fixture(str(directory))

    def setUp(self):
        WORK.mkdir(parents=True, exist_ok=True)


class TestSeeds(unittest.TestCase):
    def setUp(self):
        self.dir = WORK / "seeds"
        shutil.rmtree(self.dir, ignore_errors=True)
        self.dir.mkdir(parents=True)

    def test_10_the_same_seed_always_gives_the_same_commit(self):
        seed = ROOT / "benchmark" / "fixtures" / "R10" / "seed"
        first = bench.build_seed(str(seed), str(self.dir / "a"))
        second = bench.build_seed(str(seed), str(self.dir / "b"))
        self.assertEqual(first, second)
        branch = subprocess.run(["git", "-C", str(self.dir / "a"), "branch", "--show-current"],
                                stdout=subprocess.PIPE, check=True).stdout.decode().strip()
        self.assertEqual(branch, "main")

    def test_11_one_changed_byte_changes_the_commit(self):
        seed = self.dir / "seed"
        shutil.copytree(ROOT / "benchmark" / "fixtures" / "R8" / "seed", seed)
        before = bench.build_seed(str(seed), str(self.dir / "a"))
        path = seed / "mathutil" / "clamp.py"
        path.write_text(path.read_text() + "\n")
        self.assertNotEqual(before, bench.build_seed(str(seed), str(self.dir / "b")))

    def test_12_file_modes_and_finder_litter_do_not_change_the_commit(self):
        seed = self.dir / "seed"
        shutil.copytree(ROOT / "benchmark" / "fixtures" / "R8" / "seed", seed)
        before = bench.build_seed(str(seed), str(self.dir / "a"))
        (seed / "mathutil" / "clamp.py").chmod(0o755)
        (seed / ".DS_Store").write_bytes(b"\0")
        self.assertEqual(before, bench.build_seed(str(seed), str(self.dir / "b")))


class TestScoring(unittest.TestCase):
    """Every reason a run is not a pass, one at a time."""

    def test_20_a_correct_in_scope_clean_run_passes(self):
        self.assertEqual(bench.score(FIXTURE, record()), (bench.PASSED, []))

    def test_21_an_oracle_failure_fails_even_when_the_launcher_says_succeeded(self):
        result, reasons = bench.score(FIXTURE, record(oracle="fail"))
        self.assertEqual(result, bench.FAILED)
        self.assertIn("oracle fail", reasons)

    def test_22_an_unresolved_oracle_is_not_a_pass(self):
        self.assertEqual(bench.score(FIXTURE, record(oracle="unresolved"))[0], bench.FAILED)

    def test_23_an_out_of_scope_change_fails(self):
        result, reasons = bench.score(FIXTURE, record(out_of_scope=["setup.py"]))
        self.assertEqual(result, bench.FAILED)
        self.assertIn("setup.py", reasons[0])

    def test_24_a_failed_outcome_fails(self):
        result, reasons = bench.score(FIXTURE, record(final_outcome="failed", exit_status=10))
        self.assertEqual(result, bench.FAILED)
        self.assertIn("final outcome failed, expected succeeded", reasons)

    def test_25_a_blocked_outcome_is_blocked_not_failed(self):
        result, _ = bench.score(FIXTURE, record(final_outcome="blocked", exit_status=11))
        self.assertEqual(result, bench.BLOCKED)

    def test_26_a_precondition_refusal_is_blocked_with_no_report(self):
        result, reasons = bench.score(FIXTURE, record(final_outcome=None, exit_status=3))
        self.assertEqual(result, bench.BLOCKED)
        self.assertIn("no completion report (dca exit 3)", reasons)

    def test_27_an_infrastructure_abort_fails(self):
        result, _ = bench.score(FIXTURE, record(final_outcome=None, exit_status=4))
        self.assertEqual(result, bench.FAILED)

    def test_28_a_killed_run_fails(self):
        result, _ = bench.score(FIXTURE, record(final_outcome=None, exit_status=None))
        self.assertEqual(result, bench.FAILED)

    def test_29_a_schema_invalid_report_fails_whatever_it_claims(self):
        result, reasons = bench.score(FIXTURE, record(report_schema_valid=False))
        self.assertEqual(result, bench.FAILED)
        self.assertIn("does not conform", reasons[0])

    def test_30_a_leaked_sandbox_fails_an_otherwise_perfect_run(self):
        result, reasons = bench.score(FIXTURE, record(cleanup="failed",
                                                      leaked_sandboxes=["dca-run-x"]))
        self.assertEqual(result, bench.FAILED)
        self.assertIn("dca-run-x", reasons[0])

    def test_31_a_leak_turns_a_blocked_run_into_a_failure(self):
        result, _ = bench.score(FIXTURE, record(final_outcome="blocked", exit_status=11,
                                                cleanup="failed", leaked_sandboxes=["x"]))
        self.assertEqual(result, bench.FAILED)

    def test_32_an_expected_block_that_blocks_passes(self):
        fixture = {"expected_disposition": "blocked"}
        self.assertEqual(bench.score(fixture, record(final_outcome="blocked", exit_status=11,
                                                     oracle="skipped"))[0], bench.PASSED)


class TestEvidence(unittest.TestCase):
    def test_40_scope_uses_the_fixture_globs(self):
        files = [{"path": "shop/pricing.py"}, {"path": "tests/test_new.py"}, {"path": "setup.py"},
                 {"path": "shop/other.py"}]
        self.assertEqual(bench.out_of_scope(files, ["shop/pricing.py", "tests/*.py"]),
                         ["setup.py", "shop/other.py"])

    def test_41_only_the_launchers_own_checks_count_as_verification(self):
        agent = {"executed_by": "agent", "result": "pass"}
        self.assertEqual(bench.verification_result(
            {"verification": {"checks": [agent]}}), "none")
        self.assertEqual(bench.verification_result({"verification": {"checks": [
            agent, {"executed_by": "launcher", "result": "fail"}]}}), "fail")
        self.assertEqual(bench.verification_result({"verification": {"checks": [
            {"executed_by": "launcher", "result": "pass"}]}}), "pass")
        self.assertEqual(bench.verification_result({"verification": None}), "none")

    def write_stream(self, lines):
        path = WORK / "events.jsonl"
        WORK.mkdir(parents=True, exist_ok=True)
        path.write_text("".join(json.dumps(line) + "\n" for line in lines))
        return str(path)

    def test_42_delegation_is_read_from_the_claude_task_tool(self):
        path = self.write_stream([
            {"type": "stream_started", "session_id": "s"},
            {"type": "tool_call", "agent_name": "root", "tool_call": {
                "id": "1", "type": "function", "function": {
                    "name": "Task", "arguments": json.dumps({"subagent_type": "dca-reviewer"})}}},
            {"type": "stream_stopped", "session_id": "s", "reason": "normal"}])
        self.assertEqual(bench.delegations(path), {"researcher": False, "reviewer": True})

    def test_43_delegation_is_read_from_docker_agent_event_labels(self):
        path = self.write_stream([
            {"type": "stream_started", "session_id": "s"},
            {"type": "tool_call", "agent_name": "root", "tool_call": {
                "id": "1", "type": "function", "function": {
                    "name": "transfer_task", "arguments": json.dumps({"agent": "researcher"})}}},
            {"type": "tool_call", "agent_name": "researcher", "tool_call": {
                "id": "2", "type": "function", "function": {
                    "name": "read_file", "arguments": json.dumps({"path": "a.py"})}}},
            {"type": "stream_stopped", "session_id": "s", "reason": "normal"}])
        self.assertEqual(bench.delegations(path), {"researcher": True, "reviewer": False})

    def test_44_no_stream_means_unknown_not_false(self):
        self.assertEqual(bench.delegations(str(WORK / "absent.jsonl")),
                         {"researcher": None, "reviewer": None})

    def test_45_metrics_come_from_the_report_and_are_null_when_absent(self):
        metrics = bench.metrics_from_report({
            "run_id": "r", "final_outcome": "succeeded",
            "limits": {"used": {"steps": 23, "retries": 1, "verification_runs": 4},
                       "limit_reached": None, "native_ceiling": None},
            "token_usage": {"total_tokens": 10, "input_tokens": 7, "output_tokens": 3,
                            "cost": 0.1, "sessions": 1},
            "change_set": {"files": [{"path": "a"}, {"path": "b"}]},
            "classification": {"value": "planned"}, "review": {"performed": True}})
        self.assertEqual((metrics["tool_calls"], metrics["retries"], metrics["changed_files"]),
                         (23, 1, 2))
        self.assertEqual(metrics["tokens"], {"total_tokens": 10, "input_tokens": 7,
                                             "output_tokens": 3, "cost": 0.1})
        self.assertEqual(metrics["classification"], "planned")
        self.assertTrue(metrics["review_performed"])
        empty = bench.metrics_from_report({"token_usage": None})
        self.assertIsNone(empty["tokens"])
        self.assertIsNone(empty["tool_calls"])


class TestBenchRuns(unittest.TestCase):
    """End to end through the `dca run` process boundary, with the fake launcher."""

    def setUp(self):
        self.dir = WORK / self.id().rsplit(".", 1)[-1]
        shutil.rmtree(self.dir, ignore_errors=True)
        (self.dir / "sbx").mkdir(parents=True)
        (self.dir / "sbx" / "state.json").write_text(json.dumps({"sandboxes": []}))
        self.sbx = sbx_module.Sbx(binary=str(FAKE_SBX),
                                  env={"DCA_FAKE_SBX_DIR": str(self.dir / "sbx")})
        os.environ["DCA_FAKE_SBX_DIR"] = str(self.dir / "sbx")
        self.logs = []

    def tearDown(self):
        os.environ.pop("DCA_FAKE_RUN", None)
        os.environ.pop("DCA_FAKE_SBX_DIR", None)

    def run_bench(self, selection="R1", backends=("claude",), **script):
        fixtures = bench.discover(selection=selection)
        if "patch" not in script and script.get("mode", "succeeded") != "precondition":
            script["patch"] = os.path.join(fixtures[0]["_dir"], fixtures[0]["golden"]["good"])
        os.environ["DCA_FAKE_RUN"] = json.dumps(script)
        runner = bench.Bench(backends, fixtures=fixtures, repo_root=str(ROOT), bench_id="b-test",
                             sbx=self.sbx, oracle=bench.run_oracle_on_host,
                             dca_command=[sys.executable, str(FAKE_DCA)], log=self.logs.append,
                             work_root=str(self.dir / "work"),
                             results_root=str(self.dir / "results"))
        return runner, runner.run()

    def test_50_a_correct_run_passes_with_metrics_from_its_own_report(self):
        runner, document = self.run_bench(delegate=["researcher"])
        run = document["runs"][0]
        self.assertEqual(run["result"], bench.PASSED, run["reasons"])
        self.assertEqual((run["oracle"], run["verification"], run["cleanup"]),
                         ("pass", "pass", "ok"))
        self.assertEqual(run["run_id"], "run-2026-09-21T04-05-06Z-a1b2c3")
        self.assertEqual(run["changed_files"], 1)
        self.assertTrue(run["researcher_invoked"])
        self.assertFalse(run["reviewer_invoked"])
        self.assertTrue(run["report_schema_valid"])
        self.assertEqual(run["classification"], "direct")
        self.assertEqual(document["summary"]["success_rate"], 1.0)

    def test_51_the_run_goes_through_the_dca_run_command_with_the_fixture_contract(self):
        fixture = bench.discover(selection="R1")[0]
        runner = bench.Bench(["codex"], fixtures=[fixture], repo_root=str(ROOT), sbx=self.sbx,
                             dca_command=["dca"], work_root=str(self.dir / "work"),
                             results_root=str(self.dir / "results"), log=self.logs.append)
        seen = []

        def invoke(argv, base):
            seen.append(argv)
            return 3, "refused"

        runner._invoke = invoke
        runner.run_one("codex", fixture, 1)
        argv = seen[0]
        self.assertEqual(argv[:2], ["dca", "run"])
        self.assertEqual(argv[argv.index("--backend") + 1], "codex")
        self.assertEqual(argv[argv.index("--trust") + 1], "trusted")
        self.assertEqual(argv[argv.index("--verify") + 1], fixture["verification"]["commands"][0])
        self.assertTrue(argv[argv.index("--task") + 1].startswith("@"))
        for forbidden in ("--allow-drift", "--ignore-uncommitted", "--approve"):
            self.assertNotIn(forbidden, argv)

    def test_52_a_wrong_answer_is_caught_by_the_oracle(self):
        fixture = bench.discover(selection="R1")[0]
        bad = os.path.join(fixture["_dir"], fixture["golden"]["bad"][0])
        _, document = self.run_bench(patch=bad)
        run = document["runs"][0]
        self.assertEqual((run["final_outcome"], run["oracle"], run["result"]),
                         ("succeeded", "fail", bench.FAILED))

    def test_53_a_failed_run_is_failed_and_the_oracle_is_skipped(self):
        _, document = self.run_bench(mode="failed")
        run = document["runs"][0]
        self.assertEqual((run["result"], run["oracle"], run["verification"]),
                         (bench.FAILED, "skipped", "fail"))

    def test_54_a_precondition_refusal_is_blocked_with_its_diagnostic(self):
        _, document = self.run_bench(mode="precondition")
        run = document["runs"][0]
        self.assertEqual((run["result"], run["exit_status"]), (bench.BLOCKED, 3))
        self.assertIn("precondition failed", run["diagnostic"])
        self.assertEqual(document["summary"]["blocked"], 1)

    def test_55_an_invalid_report_is_a_failure(self):
        _, document = self.run_bench(mode="invalid-report")
        run = document["runs"][0]
        self.assertFalse(run["report_schema_valid"])
        self.assertEqual(run["result"], bench.FAILED)

    def test_56_a_leaked_sandbox_is_recorded_failed_and_removed(self):
        _, document = self.run_bench(leak=True)
        run = document["runs"][0]
        self.assertEqual(run["cleanup"], "failed")
        self.assertEqual(run["leaked_sandboxes"], ["dca-run-2026-09-21T04-05-06Z-a1b2c3"])
        self.assertEqual(run["result"], bench.FAILED)
        self.assertEqual(document["summary"]["cleanup_failures"], 1)
        calls = [json.loads(line)["argv"][1:3] for line in
                 (self.dir / "sbx" / "calls.jsonl").read_text().splitlines()]
        self.assertIn(["rm", "--force"], calls)

    def test_57_results_are_written_as_json_and_markdown(self):
        runner, document = self.run_bench(selection="R1,R8", backends=("claude", "codex"))
        on_disk = json.loads((Path(runner.results_dir) / "benchmark.json").read_text())
        self.assertEqual(on_disk["summary"]["total"], 4)
        self.assertEqual(set(on_disk["summary"]["by_backend"]), {"claude", "codex"})
        self.assertEqual([(r["backend"], r["fixture"]) for r in on_disk["runs"]],
                         [("claude", "R1"), ("claude", "R8"), ("codex", "R1"), ("codex", "R8")])
        markdown = (Path(runner.results_dir) / "benchmark.md").read_text()
        self.assertIn("| Backend | Fixture | Result |", markdown)
        self.assertIn("| claude |", markdown)
        self.assertIn("Success rate", markdown)

    def test_58_repeat_runs_each_fixture_that_many_times(self):
        fixtures = bench.discover(selection="R8")
        os.environ["DCA_FAKE_RUN"] = json.dumps(
            {"patch": os.path.join(fixtures[0]["_dir"], fixtures[0]["golden"]["good"])})
        runner = bench.Bench(["claude"], fixtures=fixtures, repeat=2, repo_root=str(ROOT),
                             sbx=self.sbx, oracle=bench.run_oracle_on_host,
                             dca_command=[sys.executable, str(FAKE_DCA)], log=self.logs.append,
                             work_root=str(self.dir / "work"),
                             results_root=str(self.dir / "results"))
        document = runner.run()
        self.assertEqual([r["repeat"] for r in document["runs"]], [1, 2])
        self.assertEqual(len({r["seed_commit"] for r in document["runs"]}), 1)


class TestSummary(unittest.TestCase):
    def test_60_rates_durations_and_counts(self):
        rows = [dict(record(), backend="claude", result=bench.PASSED, duration_seconds=10),
                dict(record(), backend="claude", result=bench.FAILED, duration_seconds=30),
                dict(record(), backend="codex", result=bench.BLOCKED, duration_seconds=20,
                     cleanup="failed")]
        summary = bench.summarize(rows)
        self.assertEqual((summary["total"], summary["passed"], summary["failed"],
                          summary["blocked"]), (3, 1, 1, 1))
        self.assertEqual(summary["success_rate"], 0.333)
        self.assertEqual((summary["duration_mean_seconds"], summary["duration_median_seconds"]),
                         (20, 20))
        self.assertEqual(summary["cleanup_failures"], 1)
        self.assertEqual(summary["by_backend"]["claude"]["success_rate"], 0.5)

    def test_61_classification_mismatches_are_counted_not_scored(self):
        rows = [dict(record(), backend="claude", result=bench.PASSED, duration_seconds=1,
                     classification="planned", expected_classification="direct")]
        self.assertEqual(bench.summarize(rows)["classification_mismatches"], 1)


class TestCommand(unittest.TestCase):
    def options(self, **overrides):
        values = {"backend": "claude", "trust": "trusted", "fixtures": None, "repeat": 1,
                  "acceptance": False}
        values.update(overrides)
        return argparse.Namespace(**values)

    def repo_with(self, fixture):
        root = WORK / "repo-root" / fixture
        shutil.rmtree(root, ignore_errors=True)
        (root / "gates").mkdir(parents=True)
        (root / "benchmark").mkdir()
        os.symlink(ROOT / "benchmark" / "fixtures", root / "benchmark" / "fixtures")
        shutil.copy(ELIGIBILITY / fixture, root / "gates" / "eligibility.json")
        return str(root)

    def test_70_acceptance_is_refused_for_this_suite(self):
        with self.assertRaises(errors.PreconditionError) as caught:
            bench.command(self.options(acceptance=True), repo_root=str(ROOT))
        self.assertIn("28-fixture", str(caught.exception))

    def test_71_untrusted_on_an_untrusted_ineligible_backend_runs_nothing(self):
        started = []
        with self.assertRaises(errors.PreconditionError) as caught:
            bench.command(self.options(trust="untrusted"),
                          repo_root=self.repo_with("trusted-only.json"),
                          bench_factory=lambda *a, **k: started.append(a))
        self.assertIn("not untrusted-eligible", str(caught.exception))
        self.assertEqual(started, [])

    def test_72_an_unavailable_backend_is_refused_before_any_run(self):
        started = []
        with self.assertRaises(errors.PreconditionError):
            bench.command(self.options(backend="both"),
                          repo_root=self.repo_with("codex-unavailable.json"),
                          bench_factory=lambda *a, **k: started.append(a))
        self.assertEqual(started, [])

    def test_73_no_matching_fixture_and_a_bad_repeat_are_usage_errors(self):
        with self.assertRaises(errors.UsageError):
            bench.command(self.options(fixtures="Z*"), repo_root=str(ROOT))
        with self.assertRaises(errors.UsageError):
            bench.command(self.options(repeat=0), repo_root=str(ROOT))

    def test_74_both_selects_both_backends_and_exit_reflects_the_score(self):
        seen = {}

        class Stub:
            def __init__(self, backends, **kwargs):
                seen["backends"] = backends
                self.results_dir = str(WORK / "stub")

            def run(self):
                return {"bench_id": "b", "dca_commit": "0" * 40, "dca_tree_clean": True,
                        "trust": "trusted", "repeat": 1, "oracle_image": None, "runs": [],
                        "summary": dict(bench.summarize([]), passed=1, total=2)}

        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            code = bench.command(self.options(backend="both"),
                                 repo_root=self.repo_with("all-eligible.json"), bench_factory=Stub)
        self.assertEqual(seen["backends"], ["claude", "codex"])
        self.assertEqual(code, 1)


class TestResultHygiene(unittest.TestCase):
    """A bench run leaves the DCA worktree clean; otherwise the next run records it as dirty."""

    @staticmethod
    def ignored(path):
        return subprocess.run(["git", "-C", str(ROOT), "check-ignore", "--quiet", "--no-index",
                               os.path.relpath(path, ROOT)], check=False).returncode == 0

    def test_80_everything_a_bench_run_writes_is_ignored_by_git(self):
        runner = bench.Bench(["claude"], repo_root=str(ROOT), sbx=object(), oracle=object())
        for path in (os.path.join(runner.results_dir, "benchmark.json"),
                     os.path.join(runner.results_dir, "benchmark.md"),
                     os.path.join(runner.work, "claude-R1-1", "run", "report.json")):
            with self.subTest(path=os.path.relpath(path, ROOT)):
                self.assertTrue(self.ignored(path))

    def test_81_committed_baselines_are_not_ignored(self):
        baselines = sorted((ROOT / "benchmark" / "baselines").glob("*"))
        self.assertTrue(baselines)
        for path in baselines + [ROOT / "benchmark" / "results" / ".gitkeep"]:
            with self.subTest(path=str(path.relative_to(ROOT))):
                self.assertFalse(self.ignored(path))


class TestReliabilityNamespace(unittest.TestCase):
    """007: the reliability suite is R1-R10; K*/M* belong to the acceptance fixtures (T079-T082)."""

    MAPPING = {**{f"K{i}": f"R{i}" for i in range(1, 9)}, "M1": "R9", "M2": "R10"}
    BASELINE = ROOT / "benchmark" / "baselines" / "reliability-2026-09-22.json"

    def setUp(self):
        self.dir = WORK / "namespace"
        shutil.rmtree(self.dir, ignore_errors=True)
        self.dir.mkdir(parents=True)

    def test_90_the_reliability_suite_is_exactly_r1_to_r10(self):
        self.assertEqual([f["id"] for f in bench.discover()],
                         [f"R{i}" for i in range(1, 11)])

    def test_91_no_committed_fixture_uses_an_acceptance_id(self):
        for directory in sorted((ROOT / "benchmark" / "fixtures").iterdir()):
            if directory.is_dir():
                with self.subTest(fixture=directory.name):
                    self.assertRegex(directory.name, r"^R([1-9]|10)$")

    def test_92_the_baseline_keeps_its_historical_ids_and_maps_them(self):
        baseline = json.loads(self.BASELINE.read_text())
        self.assertEqual(baseline["fixture_ids"]["historical_to_current"], self.MAPPING)
        recorded = {run["fixture"] for section in ("campaign", "superseded", "focused_reruns")
                    for entry in baseline[section] for run in entry["runs"]}
        self.assertTrue(recorded)
        self.assertLessEqual(recorded, set(self.MAPPING), "history must not be rewritten to R IDs")

    def test_93_each_renamed_fixture_is_the_one_the_baseline_ran(self):
        # A seed builds to a commit determined by its bytes alone, so the same commit proves the
        # renamed fixture starts every run exactly where the historical fixture did.
        baseline = json.loads(self.BASELINE.read_text())
        recorded = {run["fixture"]: run["seed_commit"]
                    for entry in baseline["campaign"] for run in entry["runs"]}
        for old, new in self.MAPPING.items():
            with self.subTest(fixture=f"{old}->{new}"):
                seed = ROOT / "benchmark" / "fixtures" / new / "seed"
                self.assertEqual(bench.build_seed(str(seed), str(self.dir / new)), recorded[old])

    def test_94_the_acceptance_contract_still_names_k_and_m(self):
        tasks = (ROOT / "specs" / "001-bounded-coding-agent" / "tasks.md").read_text()
        for phrase in ("Create fixtures `benchmark/fixtures/K1`–`K4`",
                       "Create fixtures `benchmark/fixtures/K5`–`K8`",
                       "Create fixtures `benchmark/fixtures/M1`, `M2`, `M4`, `M6`"):
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, tasks)
        pattern = json.loads(Path(bench.FIXTURE_SCHEMA).read_text())["properties"]["id"]["pattern"]
        acceptance = ([f"K{i}" for i in range(1, 9)] + [f"M{i}" for i in range(1, 7)]
                      + [f"F{i}" for i in range(1, 7)]
                      + ["S1", "S2", "S3", "S4", "S5a", "S5b", "S6", "S7", "S8"])
        self.assertEqual([i for i in acceptance if not re.fullmatch(pattern, i)], [])


if __name__ == "__main__":
    unittest.main()
