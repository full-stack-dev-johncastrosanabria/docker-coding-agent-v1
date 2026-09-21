"""`dca` command-line interface (tasks.md T065, T072; contracts/launcher-cli.md).

Standard library only. The flag surface is EXACTLY the contract's, and what is absent from it
matters as much as what is present:

  * there is no `--dry-run`, no `--skip-gates`, no `--ignore-g11` and no `--unsafe`;
  * there is no `--safety`: every native Codex execution runs `--safety strict`, and no caller can
    change that;
  * there is no way to point the run at a different kit directory.

Each of those is rejected as a **usage error (exit 2)** with a message saying so, rather than being
silently ignored. A flag that is quietly ignored teaches the person that it worked.

EXIT STATUS IS THE ONLY THING THIS LAYER DECIDES. 0/10/11 come from the finalized report, 2 from a
bad invocation, 3 from a precondition failure and 4 from an infrastructure abort. The CLI prints
the diagnostic and returns the number; it never converts one kind of failure into another, because
exit 4 and exit 11 mean different things to whoever is reading them - infrastructure broke, versus
the agent could not finish the work.
"""

import argparse
import os
import subprocess
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.dirname(os.path.dirname(_HERE))


def _sideload(name, filename):
    import importlib.util

    module = sys.modules.get(name)
    if module is None:
        spec = importlib.util.spec_from_file_location(name, os.path.join(_HERE, filename))
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
    return module


try:
    from . import launcher as launcher_module
    from .errors import InfraAbort, PreconditionError, UsageError
except ImportError:  # loaded by path in tests
    launcher_module = _sideload("dca_launcher", "launcher.py")
    _errors = _sideload("dca_errors", "errors.py")
    InfraAbort, PreconditionError, UsageError = (
        _errors.InfraAbort, _errors.PreconditionError, _errors.UsageError)

#: Options that must never exist. Each is rejected by name so the refusal is explicit.
FORBIDDEN_OPTIONS = {
    "--dry-run": "dca has no dry-run mode: a run either happens under the full policy or not at all",
    "--skip-gates": "gate evidence can never be skipped",
    "--ignore-g11": "a partial G11 is never accepted",
    "--unsafe": "there is no unsafe mode",
    "--safety": "every native Codex execution runs --safety strict and it cannot be changed",
    "--no-verify": "verification cannot be turned off",
    "--force": "there is no force mode",
    "--kit-dir": "the kit directory is the trusted staged kit and cannot be redirected",
}


class _Parser(argparse.ArgumentParser):
    """argparse exits 2 on a bad invocation, which is already the contract's usage code."""

    def error(self, message):
        self.print_usage(sys.stderr)
        print(f"dca: {message}", file=sys.stderr)
        raise SystemExit(2)


def build_parser():
    parser = _Parser(prog="dca", description="Bounded coding agent in a disposable sandbox.")
    commands = parser.add_subparsers(dest="command", required=True)

    run = commands.add_parser("run", help="run one bounded coding task")
    run.add_argument("--repo", required=True, help="path to the git repository (never mounted)")
    run.add_argument("--task", required=True, help="the task text, or @file to read it from a file")
    run.add_argument("--ref", default=None,
                     help="local branch to deliver (default: HEAD, which must be attached)")
    run.add_argument("--backend", default="claude", choices=["claude", "codex"])
    run.add_argument("--trust", default="untrusted", choices=["trusted", "untrusted"],
                     help="default: untrusted (FR-029a)")
    run.add_argument("--criteria", default=None, help="@file of acceptance criteria, one per line")
    run.add_argument("--verify", action="append", default=[], metavar="CMD",
                     help="a required verification command; repeatable")
    run.add_argument("--approve", action="append", default=[], metavar="REQUEST-ID",
                     help="grant a prior run's approval request for THIS run; repeatable")
    run.add_argument("--approval-report", default=None,
                     help="the originating report, when it is not at the default location")
    run.add_argument("--ignore-uncommitted", action="store_true",
                     help="proceed from --ref despite a dirty checkout (names are recorded)")
    run.add_argument("--out", default=None, help="output directory (default: <repo>/../.dca-runs/)")
    run.add_argument("--allow-drift", action="store_true",
                     help="permit version drift; the run is then not acceptance-eligible")

    commands.add_parser("verify", help="run the repository verification checks")

    bench = commands.add_parser("bench", help="run the deterministic benchmark")
    bench.add_argument("--backend", default="claude", choices=["claude", "codex"])
    bench.add_argument("--trust", default="trusted", choices=["trusted", "untrusted"])
    bench.add_argument("--fixtures", default=None, help="glob selecting fixtures")
    bench.add_argument("--repeat", type=int, default=1, help="runs per fixture (acceptance: 3)")
    bench.add_argument("--acceptance", action="store_true",
                       help="acceptance mode: requires committed thresholds and passed gates")
    return parser


def _reject_forbidden(argv):
    for argument in argv:
        name = argument.split("=", 1)[0]
        if name in FORBIDDEN_OPTIONS:
            raise UsageError(f"{name} is not an option: {FORBIDDEN_OPTIONS[name]}")


def read_at_file(value, what):
    """`@path` reads the file; anything else is the literal value."""
    if not isinstance(value, str) or not value.startswith("@"):
        return value
    path = value[1:]
    try:
        with open(path, "rb") as handle:
            raw = handle.read()
    except OSError as exc:
        raise UsageError(f"{what} file could not be read: {exc}") from exc
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise UsageError(f"{what} file is not valid UTF-8") from exc


def _criteria(value):
    if value is None:
        return []
    text = read_at_file(value, "--criteria")
    return [line for line in text.replace("\r\n", "\n").split("\n") if line]


def command_run(options, launcher_factory=None):
    request = launcher_module.RunRequest(
        repo=options.repo,
        task=read_at_file(options.task, "--task"),
        ref=options.ref,
        backend=options.backend,
        trust=options.trust,
        criteria=_criteria(options.criteria),
        verify=list(options.verify),
        approve=list(options.approve),
        approval_report=options.approval_report,
        ignore_uncommitted=options.ignore_uncommitted,
        out=options.out,
        allow_drift=options.allow_drift,
    )
    launcher = (launcher_factory or launcher_module.Launcher)(request)
    return launcher.run()


def command_verify(_options, repo_root=None):
    script = os.path.join(repo_root or _REPO_ROOT, "scripts", "verify.sh")
    return subprocess.run(["sh", script], check=False).returncode


def command_bench(_options):
    print("dca bench: the benchmark runner arrives with the benchmark fixtures (tasks.md T076).",
          file=sys.stderr)
    return 2


def main(argv=None, launcher_factory=None, repo_root=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    try:
        _reject_forbidden(argv)
        options = build_parser().parse_args(argv)
        if options.command == "run":
            return command_run(options, launcher_factory)
        if options.command == "verify":
            return command_verify(options, repo_root)
        return command_bench(options)
    except UsageError as exc:
        print(f"dca: {exc}", file=sys.stderr)
        return exc.exit_code
    except PreconditionError as exc:
        print(f"dca: precondition failed: {exc}", file=sys.stderr)
        return exc.exit_code
    except InfraAbort as exc:
        print(f"dca: infrastructure abort: {exc}", file=sys.stderr)
        print("dca: no completion report was written and no dca/<run-id> branch was created.",
              file=sys.stderr)
        return exc.exit_code
    except SystemExit as exc:
        return exc.code if isinstance(exc.code, int) else 2


if __name__ == "__main__":
    sys.exit(main())
