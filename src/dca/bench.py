"""`dca bench`: repeatable reliability runs over the repository-owned fixtures (tasks.md T076).

Standard library only. A benchmark here measures THE PRODUCT, so every fixture run is one real
`bin/dca run` - the same entry point a developer uses, as a separate process - and nothing in this
module talks to a model, a sandbox template or a provider directly. Eligibility, policy, the
mountless sandbox, host limits, the launcher's own verification, quarantined retrieval and cleanup
all happen exactly as they would for a developer, because they happen in the same code.

What this module adds is only what a benchmark needs on top of a run:

  * a **deterministic repository** per fixture, built from `seed/` with fixed commit metadata, so
    the same fixture always starts from the same commit SHA;
  * an **oracle** that decides whether the delivered branch is actually correct, using hidden tests
    the agent never saw plus the fixture's original tests restored (so a weakened test cannot pass).
    Oracles execute agent-produced code, so they run in a `--network none` container of the pinned
    sandbox base image, never on the host;
  * a **scope check** of the delivered change set against the fixture's `allowed_change_scope`;
  * a **cleanup check**: the sandbox list before and after every run, so a leaked sandbox is
    visible in the results instead of silently accumulating;
  * **metrics** read from the run's own `report.json` and `events.jsonl`. Nothing is estimated:
    a value that is not in the run's evidence is recorded as null.

THE ACCEPTANCE PROTOCOL IS NOT IMPLEMENTED HERE. contracts/launcher-cli.md defines `--acceptance`
over a 28-fixture suite with committed thresholds; this suite is a smaller reliability set, so
`--acceptance` is refused (exit 3) rather than scored against a denominator it does not have.
"""

import datetime
import fnmatch
import json
import os
import re
import secrets
import shutil
import signal
import statistics
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
    from . import eligibility as eligibility_module
    from . import events as events_module
    from . import jsonschema
    from . import report as report_module
    from .errors import PreconditionError, UsageError
    from .sbx import Sbx, SbxError
except ImportError:  # loaded by path in tests
    eligibility_module = _sideload("dca_eligibility", "eligibility.py")
    events_module = _sideload("dca_events", "events.py")
    jsonschema = _sideload("dca_jsonschema", "jsonschema.py")
    report_module = _sideload("dca_report", "report.py")
    _errors = _sideload("dca_errors", "errors.py")
    PreconditionError, UsageError = _errors.PreconditionError, _errors.UsageError
    _sbx = _sideload("dca_sbx", "sbx.py")
    Sbx, SbxError = _sbx.Sbx, _sbx.SbxError

BENCHMARK_DIR = os.path.join(_REPO_ROOT, "benchmark")
FIXTURE_SCHEMA = os.path.join(_REPO_ROOT, "specs", "001-bounded-coding-agent", "contracts",
                              "fixture.schema.json")

#: Fixed seed metadata (benchmark/FORMAT.md): identical `seed/` trees give identical commit SHAs.
SEED_BRANCH = "main"
SEED_ENV = {
    "GIT_AUTHOR_NAME": "dca bench", "GIT_AUTHOR_EMAIL": "bench@dca.invalid",
    "GIT_COMMITTER_NAME": "dca bench", "GIT_COMMITTER_EMAIL": "bench@dca.invalid",
    "GIT_AUTHOR_DATE": "2000-01-01T00:00:00Z", "GIT_COMMITTER_DATE": "2000-01-01T00:00:00Z",
    "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull,
}

#: A fixture's own ceiling on top of the run's wall-clock host limit: provisioning, retrieval and
#: cleanup are outside the agent's clock but inside this one.
RUN_TIMEOUT_SECONDS = 2400
ORACLE_TIMEOUT_SECONDS = 300

PASSED, FAILED, BLOCKED = "passed", "failed", "blocked"
DELEGATION_TOOLS = frozenset({"task", "agent", "transfer_task", "delegate"})


# --- fixtures ------------------------------------------------------------------------------------


def _natural_key(identifier):
    return [int(part) if part.isdigit() else part for part in re.split(r"(\d+)", identifier)]


def load_fixture(directory, schema=None):
    """One `fixture.yaml` (JSON-compatible YAML), schema-checked, with its paths resolved."""
    path = os.path.join(directory, "fixture.yaml")
    with open(path, encoding="utf-8") as handle:
        document = json.load(handle)
    problems = jsonschema.validate(document, schema or _load_json(FIXTURE_SCHEMA))
    if problems:
        raise ValueError(f"{path} does not conform to fixture.schema.json: " + "; ".join(problems))
    if document["id"] != os.path.basename(directory):
        raise ValueError(f"{path}: id {document['id']!r} does not match its directory")
    for relative in (document["oracle"], document.get("seed", "seed")):
        if not os.path.exists(os.path.join(directory, relative)):
            raise ValueError(f"{path}: {relative} does not exist")
    document["_dir"] = directory
    return document


def discover(root=None, selection=None):
    """Every fixture under `root`, in natural id order, filtered by comma-separated id globs."""
    root = root or os.path.join(BENCHMARK_DIR, "fixtures")
    schema = _load_json(FIXTURE_SCHEMA)
    fixtures = [load_fixture(os.path.join(root, name), schema)
                for name in sorted(os.listdir(root), key=_natural_key)
                if os.path.isfile(os.path.join(root, name, "fixture.yaml"))]
    if selection:
        patterns = [item.strip() for item in selection.split(",") if item.strip()]
        fixtures = [f for f in fixtures if any(fnmatch.fnmatchcase(f["id"], p) for p in patterns)]
    return fixtures


def build_seed(seed_dir, destination):
    """A single-branch `main` repository from `seed_dir`. Returns the commit SHA.

    File contents are copied without their modes and commit metadata is fixed, so the SHA depends
    on the seed's bytes alone - that SHA is the fixture's reproducible identity.
    """
    os.makedirs(destination)
    for current, directories, files in os.walk(seed_dir):
        directories.sort()
        relative = os.path.relpath(current, seed_dir)
        target = os.path.normpath(os.path.join(destination, relative))
        os.makedirs(target, exist_ok=True)
        for name in sorted(files):
            if name == ".DS_Store":
                continue
            shutil.copyfile(os.path.join(current, name), os.path.join(target, name))
    environment = dict(os.environ, **SEED_ENV)
    for argv in (["init", "--quiet", f"--initial-branch={SEED_BRANCH}"],
                 ["config", "core.autocrlf", "false"],
                 ["config", "core.fileMode", "false"],
                 ["add", "--all"],
                 ["commit", "--quiet", "--no-gpg-sign", "-m", "benchmark seed"]):
        _git(destination, *argv, env=environment)
    return _git(destination, "rev-parse", "HEAD", env=environment).strip()


# --- oracles ---------------------------------------------------------------------------------------


def oracle_image(versions):
    """The pinned Claude sandbox base, by digest: one oracle environment for every backend."""
    base = (versions.get("sandbox_bases") or {}).get("claude") or {}
    if not base.get("base") or not base.get("version"):
        return None
    return f"{base['base']}@{base['version']}"


def run_oracle_on_host(fixture, candidate_dir, timeout=ORACLE_TIMEOUT_SECONDS):
    """For repository-owned candidates only (seed, golden patches): never for agent output."""
    command = ["sh", os.path.join(fixture["_dir"], fixture["oracle"])]
    environment = dict(os.environ, CANDIDATE_DIR=candidate_dir, FIXTURE_DIR=fixture["_dir"])
    return _oracle_process(command, environment, timeout)


def container_oracle(image, docker="docker", benchmark_dir=BENCHMARK_DIR):
    """An oracle executor that runs agent-produced code offline, in the pinned base image."""
    def execute(fixture, candidate_dir, timeout=ORACLE_TIMEOUT_SECONDS):
        if not image:
            return "unresolved", "no pinned sandbox base to run the oracle in"
        fixture_path = f"/benchmark/fixtures/{fixture['id']}"
        command = [docker, "run", "--rm", "--network", "none", "--memory", "1g",
                   "--pids-limit", "512",
                   "-v", f"{os.path.abspath(candidate_dir)}:/candidate:ro",
                   "-v", f"{os.path.abspath(benchmark_dir)}:/benchmark:ro",
                   "-e", "CANDIDATE_DIR=/candidate", "-e", f"FIXTURE_DIR={fixture_path}",
                   "-w", "/tmp", "--entrypoint", "sh", image, f"{fixture_path}/{fixture['oracle']}"]
        return _oracle_process(command, None, timeout)
    return execute


def _oracle_process(command, environment, timeout):
    try:
        proc = subprocess.run(command, env=environment, stdin=subprocess.DEVNULL,
                              stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=timeout,
                              check=False)
    except subprocess.TimeoutExpired:
        return "unresolved", f"the oracle did not finish within {timeout}s"
    except OSError as exc:
        return "unresolved", f"the oracle could not start: {exc}"
    detail = proc.stdout.decode("utf-8", "replace").strip()[-600:]
    return ("pass" if proc.returncode == 0 else "fail"), detail


# --- run evidence ----------------------------------------------------------------------------------


def out_of_scope(files, allowed):
    """Changed paths that match none of the fixture's `allowed_change_scope` globs."""
    return sorted(entry["path"] for entry in files or []
                  if not any(fnmatch.fnmatchcase(entry["path"], glob) for glob in allowed))


def delegations(events_path):
    """Which sub-agents the root actually invoked, read from the typed event stream.

    Docker Agent labels every event with the agent that produced it; Claude Code delegates through
    its Task/Agent tool with a `subagent_type`. Either signal counts; neither is inferred.
    """
    found = {"researcher": False, "reviewer": False}
    if not os.path.isfile(events_path):
        return {key: None for key in found}
    for record in events_module.analyze_file(events_path).tool_calls:
        names = [str(record.agent or "")]
        if record.name.lower() in DELEGATION_TOOLS and isinstance(record.arguments, dict):
            names += [str(record.arguments.get(key) or "")
                      for key in ("subagent_type", "agent", "agent_name")]
        for role in found:
            if any(role in name.lower() for name in names):
                found[role] = True
    return found


def verification_result(report):
    """The launcher's own re-execution on the final state; the agent's claims are not counted."""
    checks = [c for c in ((report.get("verification") or {}).get("checks") or [])
              if c.get("executed_by") == "launcher"]
    if not checks:
        return "none"
    return "pass" if all(c.get("result") == "pass" for c in checks) else "fail"


def score(fixture, record):
    """PASSED / FAILED / BLOCKED plus every reason, from the evidence already in `record`.

    Passing needs all of it: a schema-valid report whose final outcome is the expected disposition,
    and - for an expected success - an oracle pass and no change outside the allowed scope. A
    leaked sandbox fails the run whatever else happened. `blocked` is reported as blocked (a
    refusal is not a wrong answer), unless blocked was the expected disposition.
    """
    reasons = []
    expected = fixture["expected_disposition"]
    outcome = record.get("final_outcome")
    if record.get("exit_status") in (2, 3, 4) or outcome is None:
        reasons.append(f"no completion report (dca exit {record.get('exit_status')})")
    elif not record.get("report_schema_valid"):
        reasons.append("the completion report does not conform to its schema")
    elif outcome != expected:
        reasons.append(f"final outcome {outcome}, expected {expected}")
    if outcome == expected == "succeeded":
        if record.get("oracle") != "pass":
            reasons.append(f"oracle {record.get('oracle')}")
        if record.get("out_of_scope"):
            reasons.append("changes outside the allowed scope: " + ", ".join(record["out_of_scope"]))
    if record.get("cleanup") != "ok":
        reasons.append("sandbox cleanup failed: " + ", ".join(record.get("leaked_sandboxes") or []))
    if not reasons:
        return PASSED, reasons
    refused = record.get("exit_status") == 3 or (outcome == "blocked" and expected != "blocked")
    return (BLOCKED if refused and record.get("cleanup") == "ok" else FAILED), reasons


def metrics_from_report(report):
    limits = report.get("limits") or {}
    used = limits.get("used") or {}
    usage = report.get("token_usage")
    classification = report.get("classification") or {}
    return {
        "run_id": report.get("run_id"),
        "final_outcome": report.get("final_outcome"),
        "primary_reason": report.get("primary_reason"),
        "tool_calls": used.get("steps"),
        "retries": used.get("retries"),
        "verification_runs": used.get("verification_runs"),
        "tokens": ({key: usage.get(key) for key in
                    ("total_tokens", "input_tokens", "output_tokens", "cost")}
                   if isinstance(usage, dict) else None),
        "changed_files": len((report.get("change_set") or {}).get("files") or []),
        "verification": verification_result(report),
        "classification": classification.get("value"),
        "review_performed": (report.get("review") or {}).get("performed"),
        "limit_reached": limits.get("limit_reached"),
        "native_ceiling": limits.get("native_ceiling"),
    }


# --- the benchmark ----------------------------------------------------------------------------------


class Bench:
    """Runs fixtures × backends × repeats, strictly one live run at a time."""

    def __init__(self, backends, trust="trusted", fixtures=None, repeat=1, repo_root=None,
                 bench_id=None, sbx=None, oracle=None, dca_command=None, clock=time.monotonic,
                 log=None, work_root=None, results_root=None):
        self.backends = list(backends)
        self.trust = trust
        self.fixtures = list(fixtures or [])
        self.repeat = repeat
        self.repo_root = repo_root or _REPO_ROOT
        self.bench_id = bench_id or new_bench_id()
        self.sbx = sbx if sbx is not None else Sbx()
        versions = eligibility_module.load_versions(
            os.path.join(self.repo_root, "runtime", "versions.yaml"))
        self.image = oracle_image(versions)
        self.oracle = oracle or container_oracle(self.image)
        self.dca_command = dca_command or [os.path.join(self.repo_root, "bin", "dca")]
        self.clock = clock
        self.log = log or (lambda message: print(message, file=sys.stderr, flush=True))
        self.work = os.path.join(work_root or os.path.join(self.repo_root, "benchmark", "work"),
                                 self.bench_id)
        self.results_dir = os.path.join(
            results_root or os.path.join(self.repo_root, "benchmark", "results"), self.bench_id)
        self.records = []

    def run(self):
        started = _utc_now()
        plan = [(backend, fixture, index) for backend in self.backends
                for fixture in self.fixtures for index in range(1, self.repeat + 1)]
        for number, (backend, fixture, index) in enumerate(plan, 1):
            self.log(f"dca bench: [{number}/{len(plan)}] {backend} {fixture['id']} "
                     f"(run {index}/{self.repeat})")
            record = self.run_one(backend, fixture, index)
            self.records.append(record)
            self.log(f"dca bench:   -> {record['result']} in {record['duration_seconds']}s"
                     + (f" ({'; '.join(record['reasons'])})" if record["reasons"] else ""))
        document = self.document(started)
        self.write(document)
        return document

    def run_one(self, backend, fixture, index):
        base = os.path.join(self.work, f"{backend}-{fixture['id']}-{index}")
        shutil.rmtree(base, ignore_errors=True)
        repo, out = os.path.join(base, "repo"), os.path.join(base, "run")
        seed_commit = build_seed(os.path.join(fixture["_dir"], fixture.get("seed", "seed")), repo)
        task_path = os.path.join(base, "task.txt")
        criteria_path = os.path.join(base, "criteria.txt")
        _write_text(task_path, fixture["task"])
        _write_text(criteria_path, "\n".join(fixture["acceptance_criteria"]) + "\n")

        argv = [*self.dca_command, "run", "--repo", repo, "--task", f"@{task_path}",
                "--backend", backend, "--trust", self._trust_for(fixture),
                "--criteria", f"@{criteria_path}", "--out", out]
        for command in (fixture["verification"].get("commands") or []):
            argv += ["--verify", command]

        before = self._sandbox_names()
        started = self.clock()
        exit_status, diagnostic = self._invoke(argv, base)
        duration = round(self.clock() - started, 1)
        leaked = sorted(self._sandbox_names() - before)
        for name in leaked:  # visible in the results, and not left to skew the next fixture
            self.sbx.remove(name)

        record = {
            "backend": backend, "fixture": fixture["id"], "category": fixture["category"],
            "repeat": index, "trust_level": self._trust_for(fixture), "seed_commit": seed_commit,
            "exit_status": exit_status, "expected_disposition": fixture["expected_disposition"],
            "expected_classification": fixture.get("expected_classification"),
            "duration_seconds": duration, "cleanup": "failed" if leaked else "ok",
            "leaked_sandboxes": leaked, "report_path": None, "report_schema_valid": None,
            "run_id": None, "final_outcome": None, "oracle": "skipped", "oracle_detail": None,
            "out_of_scope": [], "researcher_invoked": None, "reviewer_invoked": None,
            "diagnostic": diagnostic,
        }
        report_path = os.path.join(out, "report.json")
        if os.path.isfile(report_path):
            report = _load_json(report_path)
            record["report_path"] = os.path.relpath(report_path, self.repo_root)
            try:
                report_module.validate(report)
                record["report_schema_valid"] = True
            except ValueError:
                record["report_schema_valid"] = False
            record.update(metrics_from_report(report))
            found = delegations(os.path.join(out, "events.jsonl"))
            record["researcher_invoked"] = found["researcher"]
            record["reviewer_invoked"] = found["reviewer"]
            change_set = report.get("change_set") or {}
            record["out_of_scope"] = out_of_scope(change_set.get("files"),
                                                  fixture["allowed_change_scope"])
            if report.get("final_outcome") == "succeeded":
                record["oracle"], record["oracle_detail"] = self._oracle(
                    fixture, repo, change_set.get("branch"), base)
        record["result"], record["reasons"] = score(fixture, record)
        return record

    def _trust_for(self, fixture):
        return "untrusted" if fixture["trust_level"] == "untrusted" else self.trust

    def _invoke(self, argv, base):
        stdout_path, stderr_path = os.path.join(base, "dca.stdout"), os.path.join(base, "dca.stderr")
        with open(stdout_path, "wb") as stdout, open(stderr_path, "wb") as stderr:
            proc = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=stdout, stderr=stderr,
                                    start_new_session=True)
            try:
                status = proc.wait(timeout=RUN_TIMEOUT_SECONDS)
            except subprocess.TimeoutExpired:
                os.killpg(proc.pid, signal.SIGKILL)
                proc.wait()
                return None, f"dca run did not finish within {RUN_TIMEOUT_SECONDS}s and was killed"
        if status in (0, 10, 11):
            return status, None
        with open(stderr_path, encoding="utf-8", errors="replace") as handle:
            return status, handle.read().strip()[-400:] or None

    def _oracle(self, fixture, repo, branch, base):
        if not branch:
            return "fail", "the run delivered no change set"
        candidate = os.path.join(base, "candidate")
        shutil.rmtree(candidate, ignore_errors=True)
        os.makedirs(candidate)
        archive = subprocess.run(["git", "-C", repo, "archive", "--format=tar", branch],
                                 stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
        if archive.returncode != 0:
            return "unresolved", archive.stderr.decode("utf-8", "replace").strip()[:300]
        subprocess.run(["tar", "-x", "-C", candidate], input=archive.stdout, check=True)
        return self.oracle(fixture, candidate)

    def _sandbox_names(self):
        try:
            return {entry.get("name") for entry in self.sbx.list_sandboxes()
                    if isinstance(entry, dict) and str(entry.get("name", "")).startswith("dca-")}
        except (SbxError, OSError):
            return set()

    def document(self, started):
        return {
            "bench_id": self.bench_id,
            "started_at": started,
            "finished_at": _utc_now(),
            "dca_commit": _git(self.repo_root, "rev-parse", "HEAD").strip(),
            "dca_tree_clean": not _git(self.repo_root, "status", "--porcelain").strip(),
            "trust": self.trust,
            "repeat": self.repeat,
            "backends": self.backends,
            "fixtures": [f["id"] for f in self.fixtures],
            "oracle_image": self.image,
            "summary": summarize(self.records),
            "runs": self.records,
        }

    def write(self, document):
        os.makedirs(self.results_dir, exist_ok=True)
        with open(os.path.join(self.results_dir, "benchmark.json"), "w", encoding="utf-8") as fh:
            json.dump(document, fh, indent=2, sort_keys=True)
            fh.write("\n")
        _write_text(os.path.join(self.results_dir, "benchmark.md"), render(document))


def summarize(records):
    def block(rows):
        durations = [r["duration_seconds"] for r in rows if r.get("duration_seconds") is not None]
        passed = sum(r["result"] == PASSED for r in rows)
        return {
            "total": len(rows),
            "passed": passed,
            "failed": sum(r["result"] == FAILED for r in rows),
            "blocked": sum(r["result"] == BLOCKED for r in rows),
            "success_rate": round(passed / len(rows), 3) if rows else None,
            "duration_mean_seconds": round(statistics.mean(durations), 1) if durations else None,
            "duration_median_seconds": (round(statistics.median(durations), 1)
                                        if durations else None),
            "cleanup_failures": sum(r.get("cleanup") != "ok" for r in rows),
            "classification_mismatches": sum(
                1 for r in rows if r.get("expected_classification") and r.get("classification")
                and r["classification"] != r["expected_classification"]),
        }

    summary = block(records)
    summary["by_backend"] = {backend: block([r for r in records if r["backend"] == backend])
                             for backend in sorted({r["backend"] for r in records})}
    return summary


def render(document):
    def cell(value):
        return "-" if value is None else str(value)

    summary = document["summary"]
    lines = [
        f"# dca bench {document['bench_id']}", "",
        f"DCA commit `{document['dca_commit'][:12]}`"
        f"{'' if document['dca_tree_clean'] else ' (uncommitted changes)'}, trust "
        f"`{document['trust']}`, {document['repeat']} run(s) per fixture, oracle image "
        f"`{cell(document['oracle_image'])}`.", "",
        "| Backend | Fixture | Result | Outcome | Duration (s) | Tool calls | Retries | Tokens "
        "| Files | Verify | Oracle | Class | Cleanup | Run |",
        "|---|---|---|---|---:|---:|---:|---:|---:|---|---|---|---|---|",
    ]
    for r in document["runs"]:
        tokens = (r.get("tokens") or {}).get("total_tokens")
        klass = cell(r.get("classification"))
        if r.get("expected_classification") and r.get("classification") not in (
                None, r["expected_classification"]):
            klass += f" (expected {r['expected_classification']})"
        lines.append(
            f"| {r['backend']} | {r['fixture']} | {r['result']} | {cell(r.get('final_outcome'))} "
            f"| {cell(r['duration_seconds'])} | {cell(r.get('tool_calls'))} "
            f"| {cell(r.get('retries'))} | {cell(tokens)} | {cell(r.get('changed_files'))} "
            f"| {cell(r.get('verification'))} | {cell(r.get('oracle'))} | {klass} "
            f"| {r['cleanup']} | {cell(r.get('run_id'))} |")
    lines += ["", "## Summary", "",
              "| Scope | Runs | Passed | Failed | Blocked | Success rate | Mean (s) | Median (s) "
              "| Cleanup failures |",
              "|---|---:|---:|---:|---:|---:|---:|---:|---:|"]
    rows = [("all", summary)] + sorted(summary["by_backend"].items())
    for name, block in rows:
        rate = "-" if block["success_rate"] is None else f"{block['success_rate']:.0%}"
        lines.append(f"| {name} | {block['total']} | {block['passed']} | {block['failed']} "
                     f"| {block['blocked']} | {rate} | {cell(block['duration_mean_seconds'])} "
                     f"| {cell(block['duration_median_seconds'])} | {block['cleanup_failures']} |")
    problems = [r for r in document["runs"] if r["reasons"]]
    if problems:
        lines += ["", "## Runs that did not pass", ""]
        lines += [f"- **{r['backend']} {r['fixture']}** ({r['result']}): " + "; ".join(r["reasons"])
                  for r in problems]
    return "\n".join(lines) + "\n"


# --- the command --------------------------------------------------------------------------------


def command(options, repo_root=None, bench_factory=Bench):
    """`dca bench`. Exit 0 when every run passed, 1 otherwise; 2 usage, 3 precondition."""
    repo_root = repo_root or _REPO_ROOT
    if options.repeat < 1:
        raise UsageError("--repeat must be at least 1")
    fixtures = discover(os.path.join(repo_root, "benchmark", "fixtures"), options.fixtures)
    if options.acceptance:
        raise PreconditionError(
            "acceptance mode needs the full 28-fixture acceptance suite and committed "
            f"benchmark/thresholds.yaml (contracts/launcher-cli.md); this suite has "
            f"{len(fixtures)} reliability fixture(s). Run without --acceptance.")
    if not fixtures:
        raise UsageError(f"no fixture matches {options.fixtures!r}")
    backends = ["claude", "codex"] if options.backend == "both" else [options.backend]
    _require_eligible(backends, options.trust, repo_root)

    bench = bench_factory(backends, trust=options.trust, fixtures=fixtures,
                          repeat=options.repeat, repo_root=repo_root)
    document = bench.run()
    print(render(document))
    print(f"dca bench: results in {os.path.relpath(bench.results_dir, repo_root)}/", file=sys.stderr)
    summary = document["summary"]
    return 0 if summary["passed"] == summary["total"] else 1


def _require_eligible(backends, trust, repo_root):
    """Refuse before anything runs when a selected backend cannot run this profile at all."""
    try:
        document = eligibility_module.load(os.path.join(repo_root, "gates", "eligibility.json"))
        for backend in backends:
            eligibility_module.check_backend_runnable(document, backend)
            if trust == "untrusted" and eligibility_module.untrusted_blockers(document, backend):
                raise eligibility_module.EvidenceProblem(
                    f"backend {backend!r} is not untrusted-eligible; dca bench --trust untrusted "
                    "runs nothing for it (fail-closed untrusted handling is covered by the "
                    "trusted-profile runs)")
    except eligibility_module.EvidenceProblem as exc:
        raise PreconditionError(str(exc)) from exc


# --- helpers ------------------------------------------------------------------------------------


def new_bench_id(now=None):
    stamp = (now or datetime.datetime.now(datetime.timezone.utc)).strftime("%Y-%m-%dT%H-%M-%SZ")
    return f"bench-{stamp}-{secrets.token_hex(2)}"


def _utc_now():
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _git(repo, *args, env=None):
    proc = subprocess.run(["git", "-C", repo, *args], stdout=subprocess.PIPE,
                          stderr=subprocess.PIPE, env=env, check=False)
    if proc.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed: "
                           f"{proc.stderr.decode('utf-8', 'replace').strip()[:300]}")
    return proc.stdout.decode("utf-8", "replace")


def _load_json(path):
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)


def _write_text(path, text):
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(text if text.endswith("\n") else text + "\n")

