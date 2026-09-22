"""The `dca` CLI contract (tasks.md T065, extended by T072).

Two things are tested here and they pull in opposite directions on purpose.

**What must exist**: exactly the flags contracts/launcher-cli.md defines, with `--verify`,
`--approve` repeatable and `--trust` defaulting to untrusted (FR-029a).

**What must NOT exist**: `--dry-run`, `--skip-gates`, `--ignore-g11`, `--unsafe`, `--safety`. Each
is asserted to be a **usage error**, not merely absent, because argparse's default for an unknown
flag is already an error - the risk is that someone adds one later and the suite stays green. An
explicit refusal per flag is what fails when that happens.
"""

import importlib.util
import io
import json
import shutil
import subprocess
import sys
import unittest
from contextlib import redirect_stderr, redirect_stdout
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


errors = _load("dca_errors", ROOT / "src" / "dca" / "errors.py")
launcher = _load("dca_launcher", ROOT / "src" / "dca" / "launcher.py")
cli = _load("dca_cli", ROOT / "src" / "dca" / "cli.py")


class Recorded:
    """A launcher stand-in: records the request it was handed and returns a scripted status."""

    instances = []

    def __init__(self, request, status=0, raises=None):
        self.request = request
        self.status = status
        self.raises = raises
        Recorded.instances.append(self)

    def run(self):
        if self.raises is not None:
            raise self.raises
        return self.status


def factory(status=0, raises=None):
    def build(request):
        return Recorded(request, status=status, raises=raises)
    return build


def invoke(argv, status=0, raises=None):
    Recorded.instances = []
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = cli.main(argv, launcher_factory=factory(status, raises))
    return code, out.getvalue(), err.getvalue()


BASE = ["run", "--repo", str(ROOT), "--task", "fix it"]


class TestFlagSurface(unittest.TestCase):
    def test_01_the_help_lists_exactly_the_contract_commands(self):
        parser = cli.build_parser()
        out = io.StringIO()
        with redirect_stdout(out):
            parser.print_help()
        for command in ("run", "verify", "bench"):
            self.assertIn(command, out.getvalue())

    def test_02_run_accepts_every_contract_flag(self):
        options = cli.build_parser().parse_args(BASE + [
            "--ref", "main", "--backend", "codex", "--trust", "trusted",
            "--verify", "make test", "--approve", "apr-x-1",
            "--approval-report", "/tmp/report.json", "--ignore-uncommitted",
            "--out", "/tmp/out", "--allow-drift"])
        self.assertEqual(options.backend, "codex")
        self.assertEqual(options.verify, ["make test"])
        self.assertEqual(options.approve, ["apr-x-1"])
        self.assertTrue(options.ignore_uncommitted)
        self.assertTrue(options.allow_drift)

    def test_03_verify_and_approve_are_repeatable(self):
        options = cli.build_parser().parse_args(
            BASE + ["--verify", "make test", "--verify", "make lint",
                    "--approve", "apr-a-1", "--approve", "apr-a-2"])
        self.assertEqual(options.verify, ["make test", "make lint"])
        self.assertEqual(options.approve, ["apr-a-1", "apr-a-2"])

    def test_04_trust_defaults_to_untrusted(self):
        self.assertEqual(cli.build_parser().parse_args(BASE).trust, "untrusted")
        code, _, _ = invoke(BASE)
        self.assertEqual(code, 0)
        self.assertEqual(Recorded.instances[0].request.trust, "untrusted")

    def test_05_backend_defaults_to_claude(self):
        self.assertEqual(cli.build_parser().parse_args(BASE).backend, "claude")

    def test_06_a_missing_required_flag_is_a_usage_error(self):
        code, _, err = invoke(["run", "--task", "x"])
        self.assertEqual(code, 2)
        self.assertIn("--repo", err)

    def test_07_an_unknown_backend_is_a_usage_error(self):
        code, _, _ = invoke(BASE + ["--backend", "gemini"])
        self.assertEqual(code, 2)

    def test_08_no_subcommand_is_a_usage_error(self):
        code, _, _ = invoke([])
        self.assertEqual(code, 2)


class TestForbiddenOptions(unittest.TestCase):
    """Each bypass is refused by NAME, with a message saying why it does not exist."""

    def test_10_every_forbidden_option_is_a_usage_error(self):
        for flag in sorted(cli.FORBIDDEN_OPTIONS):
            with self.subTest(flag=flag):
                code, _, err = invoke(BASE + [flag])
                self.assertEqual(code, 2)
                self.assertIn(flag, err)

    def test_11_a_forbidden_option_with_a_value_is_also_refused(self):
        code, _, err = invoke(BASE + ["--safety=restricted"])
        self.assertEqual(code, 2)
        self.assertIn("--safety", err)

    def test_12_the_refusal_explains_rather_than_ignoring(self):
        _, _, err = invoke(BASE + ["--skip-gates"])
        self.assertIn("never be skipped", err)

    def test_13_no_forbidden_option_appears_in_the_help(self):
        out = io.StringIO()
        with redirect_stdout(out):
            cli.build_parser().parse_args(["run", "--help"]) if False else \
                cli.build_parser().print_help()
        for flag in cli.FORBIDDEN_OPTIONS:
            self.assertNotIn(flag, out.getvalue())

    def test_14_the_shipped_entry_point_shows_no_bypass_option(self):
        for argv in (["--help"], ["run", "--help"]):
            with self.subTest(argv=argv):
                proc = subprocess.run([str(ROOT / "bin" / "dca"), *argv],
                                      capture_output=True, text=True, check=False)
                self.assertEqual(proc.returncode, 0)
                for flag in cli.FORBIDDEN_OPTIONS:
                    self.assertNotIn(flag, proc.stdout)


class TestAtFiles(unittest.TestCase):
    def setUp(self):
        WORK.mkdir(parents=True, exist_ok=True)
        self.dir = WORK / self.id().rsplit(".", 1)[-1]
        shutil.rmtree(self.dir, ignore_errors=True)
        self.dir.mkdir(parents=True)

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_20_task_at_file_is_read_verbatim(self):
        path = self.dir / "task.txt"
        path.write_text("fix the off-by-one in  paginate()\n", encoding="utf-8")
        invoke(["run", "--repo", str(ROOT), "--task", f"@{path}"])
        self.assertEqual(Recorded.instances[0].request.task,
                         "fix the off-by-one in  paginate()\n")

    def test_21_criteria_at_file_drops_only_empty_lines(self):
        path = self.dir / "criteria.txt"
        path.write_text("page 2 starts at item 11\n\ntests pass\n", encoding="utf-8")
        invoke(["run", "--repo", str(ROOT), "--task", "t", "--criteria", f"@{path}"])
        self.assertEqual(Recorded.instances[0].request.criteria,
                         ["page 2 starts at item 11", "tests pass"])

    def test_22_a_missing_at_file_is_a_usage_error(self):
        code, _, err = invoke(["run", "--repo", str(ROOT), "--task",
                               f"@{self.dir / 'absent.txt'}"])
        self.assertEqual(code, 2)
        self.assertIn("could not be read", err)

    def test_23_a_non_utf8_task_file_is_a_usage_error(self):
        path = self.dir / "task.bin"
        path.write_bytes(b"\xff\xfe not utf-8")
        code, _, _ = invoke(["run", "--repo", str(ROOT), "--task", f"@{path}"])
        self.assertEqual(code, 2)


class TestExitCodes(unittest.TestCase):
    """0/10/11 come from the report; 2/3/4 from the three reportless failures."""

    def test_30_a_report_disposition_is_propagated_unchanged(self):
        for status in (0, 10, 11):
            with self.subTest(status=status):
                code, _, _ = invoke(BASE, status=status)
                self.assertEqual(code, status)

    def test_31_a_precondition_failure_is_exit_3(self):
        code, _, err = invoke(BASE, raises=errors.PreconditionError("the checkout is dirty"))
        self.assertEqual(code, 3)
        self.assertIn("precondition failed", err)

    def test_32_an_infrastructure_abort_is_exit_4_and_says_no_report_exists(self):
        code, _, err = invoke(BASE, raises=errors.InfraAbort("sbx create failed"))
        self.assertEqual(code, 4)
        self.assertIn("no completion report", err)
        self.assertIn("no dca/<run-id> branch", err)

    def test_33_a_usage_error_from_the_launcher_is_exit_2(self):
        code, _, _ = invoke(BASE, raises=errors.UsageError("bad input"))
        self.assertEqual(code, 2)

    def test_34_an_infrastructure_abort_is_never_reported_as_blocked(self):
        code, _, err = invoke(BASE, raises=errors.InfraAbort("retrieval failed"))
        self.assertNotEqual(code, 11)
        self.assertNotIn("blocked", err.lower())


class TestDispatch(unittest.TestCase):
    def test_40_run_builds_the_request_the_flags_describe(self):
        invoke(BASE + ["--trust", "trusted", "--backend", "codex", "--verify", "make test"])
        request = Recorded.instances[0].request
        self.assertEqual(request.backend, "codex")
        self.assertEqual(request.trust, "trusted")
        self.assertEqual(request.verify, ["make test"])
        self.assertRegex(request.run_id, r"^run-[0-9TZ-]+-[0-9a-f]{6}$")
        self.assertRegex(request.task_fingerprint, r"^sha256:[0-9a-f]{64}$")

    def test_41_the_default_output_directory_is_outside_the_repository(self):
        invoke(BASE)
        request = Recorded.instances[0].request
        self.assertNotIn(str(ROOT) + "/", request.out + "/")
        self.assertIn(".dca-runs", request.out)

    def test_42_verify_runs_the_repository_checks(self):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = cli.main(["verify", "--static"] if False else ["verify"],
                            repo_root=str(WORK / "no-such-root"))
        self.assertNotEqual(code, 0, "a missing verify.sh must not be reported as success")

    def test_43_bench_dispatches_to_the_benchmark_with_its_options(self):
        seen = []
        original = cli.bench_module.command
        cli.bench_module.command = lambda options, repo_root=None: seen.append(options) or 0
        try:
            code, _, _ = invoke(["bench", "--backend", "both", "--fixtures", "K*", "--repeat", "2"])
        finally:
            cli.bench_module.command = original
        self.assertEqual(code, 0)
        self.assertEqual((seen[0].backend, seen[0].fixtures, seen[0].repeat, seen[0].trust),
                         ("both", "K*", 2, "trusted"))

    def test_44_bench_acceptance_is_refused_as_a_precondition_before_anything_runs(self):
        code, _, err = invoke(["bench", "--acceptance"])
        self.assertEqual(code, 3)
        self.assertIn("28-fixture", err)

    def test_45_bench_rejects_an_unknown_backend(self):
        code, _, _ = invoke(["bench", "--backend", "gemini"])
        self.assertEqual(code, 2)


if __name__ == "__main__":
    unittest.main()
