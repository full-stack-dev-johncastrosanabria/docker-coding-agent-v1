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

CONVENIENCE NEVER GRANTS AUTHORITY. `--repo` defaults to the repository containing the current
directory, and backend, trust and verification commands resolve as: command line, then the
checkout's local config (`<git-dir>/dca/config.toml`, see project_config), then the safe default.
Trust therefore becomes `trusted` only by the developer's own explicit choice. Every path ends in
the same `Launcher`, with the same preconditions, eligibility, policy and cleanup.
"""

import argparse
import os
import subprocess
import sys
import time

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
    from . import bench as bench_module
    from . import console
    from . import eligibility as eligibility_module
    from . import launcher as launcher_module
    from . import project_config
    from . import source as source_module
    from .errors import InfraAbort, PreconditionError, UsageError
    from .sbx import Sbx, SbxError
except ImportError:  # loaded by path in tests
    bench_module = _sideload("dca_bench", "bench.py")
    console = _sideload("dca_console", "console.py")
    eligibility_module = _sideload("dca_eligibility", "eligibility.py")
    launcher_module = _sideload("dca_launcher", "launcher.py")
    project_config = _sideload("dca_project_config", "project_config.py")
    source_module = _sideload("dca_source", "source.py")
    _errors = _sideload("dca_errors", "errors.py")
    InfraAbort, PreconditionError, UsageError = (
        _errors.InfraAbort, _errors.PreconditionError, _errors.UsageError)
    _sbx = _sideload("dca_sbx", "sbx.py")
    Sbx, SbxError = _sbx.Sbx, _sbx.SbxError

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

    # Defaults of None mean "not given": the real default is resolved in _resolve_settings, after
    # the local config, so the precedence command line > local config > safe default holds.
    run = commands.add_parser(
        "run", help="run one bounded coding task",
        description="Run one bounded coding task in a disposable sandbox. The task is the quoted "
                    "positional argument or --task; unset options come from the checkout's local "
                    "config (dca init), then from the safe defaults.")
    run.add_argument("task_text", nargs="?", metavar="TASK",
                     help="the task text in quotes, or @file to read it from a file; a one-word "
                          "task that starts with '-' goes after --, e.g. dca run -- -fix")
    run.add_argument("--repo", default=None,
                     help="path to the git repository (default: the repository containing the "
                          "current directory; never mounted)")
    run.add_argument("--task", default=None,
                     help="the task text, or @file to read it from a file (instead of TASK)")
    run.add_argument("--ref", default=None,
                     help="local branch to deliver (default: HEAD, which must be attached)")
    run.add_argument("--backend", default=None, choices=["claude", "codex"],
                     help="default: local config, then claude")
    run.add_argument("--trust", default=None, choices=["trusted", "untrusted"],
                     help="default: local config, then untrusted (FR-029a)")
    run.add_argument("--criteria", default=None, help="@file of acceptance criteria, one per line")
    run.add_argument("--verify", action="append", default=[], metavar="CMD",
                     help="a required verification command; repeatable; replaces the local "
                          "config's commands")
    run.add_argument("--approve", action="append", default=[], metavar="REQUEST-ID",
                     help="grant a prior run's approval request for THIS run; repeatable")
    run.add_argument("--approval-report", default=None,
                     help="the originating report, when it is not at the default location")
    run.add_argument("--ignore-uncommitted", action="store_true",
                     help="proceed from --ref despite a dirty checkout (names are recorded)")
    run.add_argument("--out", default=None, help="output directory (default: <repo>/../.dca-runs/)")
    run.add_argument("--allow-drift", action="store_true",
                     help="permit version drift; the run is then not acceptance-eligible")

    init = commands.add_parser(
        "init", help="create this checkout's local DCA config",
        description="Write <git-dir>/dca/config.toml for the repository containing the current "
                    "directory. The file is never committed, cloned or sent to the sandbox.")
    init.add_argument("--repo", default=None,
                      help="path to the git repository (default: the current one)")
    init.add_argument("--backend", default=project_config.DEFAULT_BACKEND,
                      choices=["claude", "codex"])
    init.add_argument("--trust", default=project_config.DEFAULT_TRUST,
                      choices=["trusted", "untrusted"],
                      help="default: untrusted; `trusted` must be chosen explicitly")
    init.add_argument("--verify", action="append", default=[], metavar="CMD",
                      help="a verification command for every run; repeatable")
    init.add_argument("--overwrite", action="store_true",
                      help="replace an existing config with different settings")

    verify = commands.add_parser("verify", help="check that DCA is ready to run")
    verify.add_argument("--repo", default=None,
                        help="the project to check (default: the current repository, if any)")

    bench = commands.add_parser(
        "bench", help="run the benchmark fixtures through real dca runs",
        description="Run every selected fixture in benchmark/fixtures/ through a real `dca run` "
                    "(real sandbox, real model backend) and write benchmark.json and "
                    "benchmark.md under benchmark/results/<bench-id>/.")
    bench.add_argument("--backend", default="claude", choices=["claude", "codex", "both"])
    bench.add_argument("--trust", default="trusted", choices=["trusted", "untrusted"])
    bench.add_argument("--fixtures", default=None,
                       help="comma-separated fixture id globs, e.g. 'R*' or 'R1,R9'")
    bench.add_argument("--repeat", type=int, default=1, help="runs per fixture (acceptance: 3)")
    bench.add_argument("--acceptance", action="store_true",
                       help="acceptance mode (28-fixture suite and committed thresholds; "
                            "refused for this reliability suite)")
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


def _resolve_settings(options, config):
    """backend, trust, verify: command line > local config > safe default, with their sources."""
    def pick(given, configured, default):
        if given:
            return given, "command line"
        if configured:
            return configured, "local config"
        return default, "default"

    configured = config or project_config.ProjectConfig(None, None)
    return {
        "backend": pick(options.backend, configured.backend, project_config.DEFAULT_BACKEND),
        "trust": pick(options.trust, configured.trust, project_config.DEFAULT_TRUST),
        "verify": pick(list(options.verify), configured.verify, []),
    }


def _task(options):
    if options.task_text is not None and options.task is not None:
        raise UsageError("give the task once: either as the quoted TASK argument or with --task, "
                         "not both")
    value = options.task if options.task is not None else options.task_text
    if value is None or (isinstance(value, str) and not value.strip()):
        raise UsageError('a task is required: dca run "describe the task" (or --task @file)')
    return read_at_file(value, "--task" if options.task is not None else "task")


def command_run(options, launcher_factory=None, config_loader=None):
    task = _task(options)
    repo = (os.path.abspath(options.repo) if options.repo
            else project_config.detect_repository())
    config = (config_loader or project_config.load)(repo)
    settings = _resolve_settings(options, config)
    request = launcher_module.RunRequest(
        repo=repo,
        task=task,
        ref=options.ref,
        backend=settings["backend"][0],
        trust=settings["trust"][0],
        criteria=_criteria(options.criteria),
        verify=list(settings["verify"][0]),
        approve=list(options.approve),
        approval_report=options.approval_report,
        ignore_uncommitted=options.ignore_uncommitted,
        out=options.out,
        allow_drift=options.allow_drift,
    )
    display = console.RunDisplay()
    verify, verify_source = settings["verify"]
    display.header(request.run_id, [
        ("Repository", request.repo, None),
        ("Backend", request.backend, settings["backend"][1]),
        ("Trust", request.trust, settings["trust"][1]),
        ("Verify", "; ".join(verify) if verify else "(none)",
         verify_source if verify else None),
    ])
    if launcher_factory is None:
        launcher = launcher_module.Launcher(request, progress=display)
    else:
        launcher = launcher_factory(request)
    started = time.monotonic()
    try:
        status = launcher.run()
    except PreconditionError as exc:
        _stopped(f"BLOCKED during Preconditions (precondition failed, exit {exc.exit_code})",
                 [str(exc), "No sandbox was created and no report was written."])
        return exc.exit_code
    except InfraAbort as exc:
        _stopped(f"FAILED during {display.failed_phase()} (infrastructure abort, "
                 f"exit {exc.exit_code})",
                 [str(exc), "no completion report was written and no dca/<run-id> branch was "
                            "created.",
                  "Cleanup: " + (display.sandbox_state() or "cleanup did not report")],
                 after_progress=bool(display.reported))
        return exc.exit_code
    for line in console.summary(request, status, display, time.monotonic() - started,
                                settings["trust"][1]):
        print(line)
    return status


def _stopped(headline, details, after_progress=False):
    print(("\n" if after_progress else "") + f"dca: {headline}", file=sys.stderr)
    for detail in details:
        print(f"  {detail}", file=sys.stderr)


def command_init(options):
    repo = (os.path.abspath(options.repo) if options.repo
            else project_config.detect_repository())
    source_module.require_repository_root(repo)
    repo = os.path.realpath(repo)
    content = project_config.render(repo, backend=options.backend, trust=options.trust,
                                    verify=options.verify)
    path, changed = project_config.write(repo, content, overwrite=options.overwrite)
    print(("DCA initialized for " if changed else "DCA is already initialized for ") + repo)
    print(f"  Config   {path}" + ("" if changed else "   (unchanged)"))
    print("           local to this checkout: never committed, cloned or sent to the sandbox")
    print(f"  Backend  {options.backend}")
    print(f"  Trust    {options.trust}")
    print("  Verify   " + ("; ".join(options.verify) if options.verify else "(none)"))
    if options.trust != "trusted":
        print()
        print("DCA V1 production execution supports trusted profiles only; untrusted runs are "
              "blocked.")
        print("Review the security model in the DCA README. If you trust this repository's "
              "content, opt in with:")
        print("  dca init --trust trusted --overwrite" + "".join(
            f" --verify {_quoted(command)}" for command in options.verify))
    print()
    print("Next:")
    print("  dca verify")
    print('  dca run "describe the task"')
    return 0


def _quoted(value):
    return "'" + value.replace("'", "'\\''") + "'"


def _repository_rows(options):
    """Repository and local-config rows for `dca verify`. Only a broken config is a failure."""
    try:
        repo = (os.path.abspath(options.repo) if options.repo
                else project_config.detect_repository())
        source_module.require_repository_root(repo)
    except (UsageError, PreconditionError) as exc:
        return [("Repository", "SKIP", "no project selected", [str(exc)], None)], False
    rows, failed = [], False
    try:
        ref, _commit = source_module.resolve_branch(repo, None)
        branch = ref.rsplit("/", 1)[-1] if ref.startswith("refs/heads/") else ref
        dirty = source_module.worktree_state(repo)
        if dirty:
            rows.append(("Repository", "WARN", f"{repo} (branch {branch})",
                         [f"{len(dirty)} uncommitted or untracked path(s); only committed "
                          "state is delivered"],
                         "Commit or stash them, or run with --ignore-uncommitted."))
        else:
            rows.append(("Repository", "PASS", f"{repo} (branch {branch}, clean)", [], None))
    except (PreconditionError, InfraAbort) as exc:
        rows.append(("Repository", "WARN", repo, [str(exc)], None))
    try:
        config = project_config.load(repo)
    except UsageError as exc:
        rows.append(("Project config", "FAIL", None, [str(exc)], None))
        return rows, True
    if config is None:
        rows.append(("Project config", "NOT SET", "no local config for this checkout", [],
                     "Run `dca init` here (see `dca init --help`)."))
    else:
        verify = config.verify or []
        rows.append(("Project config", "PASS",
                     f"backend {config.backend or project_config.DEFAULT_BACKEND} · trust "
                     f"{config.trust or project_config.DEFAULT_TRUST} · {len(verify)} "
                     "verification command(s)", [],
                     None if (config.trust or project_config.DEFAULT_TRUST) == "trusted" else
                     "Untrusted runs are blocked in V1; opt in with `dca init --trust trusted "
                     "--overwrite` if you trust this repository."))
    return rows, failed


def command_verify(options, repo_root=None, sbx=None):
    root = repo_root or _REPO_ROOT
    script = os.path.join(root, "scripts", "verify.sh")
    if not os.path.isfile(script):
        print(f"dca: verify: {script} is missing, so nothing was checked", file=sys.stderr)
        return 1
    print("DCA verify: checking the installation, the environment and the backends...",
          file=sys.stderr, flush=True)
    proc = subprocess.run(["sh", script], stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                          check=False, cwd=root)
    failures, notes = console.parse_verify_output(proc.stdout.decode("utf-8", "replace"))
    by_category = {}
    for category, message in failures:
        by_category.setdefault(category, []).append(message)

    rows, repo_failed = _repository_rows(options)
    sbx = sbx if sbx is not None else Sbx()
    leftover = None
    try:
        leftover = sorted(entry.get("name") for entry in sbx.list_sandboxes()
                          if str(entry.get("name", "")).startswith("dca-"))
    except (SbxError, OSError, ValueError):
        pass
    for key, label in console.VERIFY_CATEGORIES:
        reasons = by_category.get(key, [])
        if key == "sandboxes" and leftover:
            reasons = reasons + [f"DCA sandbox(es) left behind: {', '.join(leftover)}"]
        if reasons:
            rows.append((label, "FAIL", None, reasons, console.ACTIONS[key]))
        elif key == "sandboxes":
            rows.append((label, "PASS", "no DCA sandboxes left behind"
                         if leftover is not None else None, [], None))
        elif key != "other":
            rows.append((label, "PASS", None, [], None))
    rows += _backend_rows(root, by_category)
    for line in console.verify_lines(rows):
        print(line)
    if notes:
        print()
        for note in notes:
            print(f"  Note: {note}")
    ready = proc.returncode == 0 and not repo_failed
    problems = sum(1 for row in rows if row[1] in ("FAIL", "NOT READY"))
    print()
    print("READY: DCA can run in this environment." if ready else
          f"NOT READY: {max(problems, 1)} problem(s) above. Fix them and rerun `dca verify`.")
    return 0 if ready else 1


def _backend_rows(root, by_category):
    try:
        document = eligibility_module.load(os.path.join(root, "gates", "eligibility.json"))
        backends = document.get("backends") or {}
    except (eligibility_module.EvidenceProblem, OSError, ValueError):
        backends = {}
    rows = []
    for name, label in (("claude", "Claude"), ("codex", "Codex")):
        entry = backends.get(name) or {}
        reasons = by_category.get(name, [])
        if not entry:
            rows.append((label, "UNKNOWN", "no eligibility evidence", [], None))
        elif not entry.get("available"):
            rows.append((label, "UNAVAILABLE", "not available per gates/eligibility.json", [],
                         None))
        elif reasons:
            rows.append((label, "NOT READY", None, reasons, console.ACTIONS[name]))
        elif entry.get("trusted_eligible"):
            rows.append((label, "READY", "trusted", [], None))
        else:
            rows.append((label, "NOT READY", "not trusted-eligible per gates/eligibility.json",
                         [], None))
    if backends:
        untrusted = [name for name, entry in backends.items() if entry.get("untrusted_eligible")]
        rows.append(("Untrusted runs", "ELIGIBLE" if untrusted else "BLOCKED",
                     ", ".join(untrusted) if untrusted else
                     "by V1 eligibility policy (expected)", [], None))
    return rows


def command_bench(options, repo_root=None):
    return bench_module.command(options, repo_root=repo_root)


def _require_checkout(repo_root):
    """DCA reads its runtime assets and gate evidence from its own checkout."""
    if not os.path.isdir(os.path.join(repo_root, "runtime")) or not os.path.isdir(
            os.path.join(repo_root, "gates")):
        raise PreconditionError(
            f"the DCA runtime assets were not found next to the code ({repo_root}). Run DCA from "
            "its source checkout: bin/dca, or an editable install (`pip install -e <checkout>`).")


def main(argv=None, launcher_factory=None, repo_root=None, config_loader=None, sbx=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    try:
        _reject_forbidden(argv)
        options = build_parser().parse_args(argv)
        if options.command == "init":
            return command_init(options)
        if options.command == "verify":
            return command_verify(options, repo_root, sbx)
        _require_checkout(repo_root or _REPO_ROOT)
        if options.command == "run":
            return command_run(options, launcher_factory, config_loader)
        return command_bench(options, repo_root)
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
