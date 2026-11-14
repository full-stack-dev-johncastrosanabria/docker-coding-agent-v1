"""T090: launcher precondition and approval re-run checks (quickstart §5 and §6), run live.

Independent of the S benchmark: it drives `bin/dca run` directly and checks exit codes and the
host-written report and grants, never an oracle. Usage:

    python3 benchmark/tools/launcher_approval_validation.py --backend claude [--backend codex]

Writes benchmark/results/<date>-launcher-approval-validation.json and exits 1 if any check fails.
"""

import argparse
import datetime
import json
import os
import re
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "src"))
from dca import bench  # noqa: E402

DCA = os.path.join(ROOT, "bin", "dca")
LIVE_TIMEOUT = 1800
DIRTY_FILE = "README.md"  # K1 has none, so the edit creates it untracked
BACKENDS = ("codex", "claude")


def git(repo, *args):
    return subprocess.run(["git", "-C", repo, *args], check=True, capture_output=True,
                          text=True).stdout.strip()


def seed(fixture_id, work):
    fixture = bench.discover(selection=fixture_id)[0]
    repo = os.path.join(work, fixture_id)
    bench.build_seed(os.path.join(fixture["_dir"], "seed"), repo)
    task = os.path.join(work, f"{fixture_id}.task.txt")
    with open(task, "w", encoding="utf-8") as handle:
        handle.write(fixture["task"])
    return repo, task, fixture["verification"]["commands"][0]


def request_id_of(request):
    """A report's request id, refused unless it has the launcher's own `apr-<run>-<n>` form."""
    match = re.fullmatch(r"apr-(run-\d{4}-\d{2}-\d{2}T\d{2}-\d{2}-\d{2}Z-[0-9a-f]{6})-(\d{1,4})",
                         str(request.get("id", "")))
    if not match:
        raise ValueError(f"not a launcher request id: {request.get('id')!r}")
    return f"apr-{match.group(1)}-{int(match.group(2))}"


def read_json(path):
    try:
        with open(path, encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, ValueError):
        return None


class Validation:
    def __init__(self, backend, work):
        self.backend, self.work, self.checks = backend, work, []

    def dca(self, repo, *args, out=None):
        command = [DCA, "run", "--repo", repo, "--backend", self.backend, *args]
        if out:
            command += ["--out", out]
        done = subprocess.run(command, capture_output=True, text=True, timeout=LIVE_TIMEOUT)
        return done.returncode, (done.stdout + done.stderr)[-1500:]

    def check(self, check_id, ok, **detail):
        self.checks.append({"id": check_id, "status": "pass" if ok else "fail", **detail})
        print(f"{self.backend} {check_id}: {'pass' if ok else 'FAIL'}", flush=True)

    def out(self, name):
        return os.path.join(self.work, "out", name)

    # --- quickstart §5 -------------------------------------------------------------------------

    def preconditions(self):
        repo, task, _ = seed("K1", self.work)
        task_arg = f"@{task}"
        with open(os.path.join(repo, DIRTY_FILE), "a", encoding="utf-8") as handle:
            handle.write("wip\n")

        out = self.out("dirty")
        code, tail = self.dca(repo, "--trust", "trusted", "--task", task_arg, out=out)
        self.check("s5.dirty_checkout_exit_3", code == 3 and read_json(f"{out}/report.json") is None,
                   exit=code, report_written=os.path.exists(f"{out}/report.json"), tail=tail[-400:])

        out = self.out("override")
        code, tail = self.dca(repo, "--trust", "trusted", "--task", task_arg,
                              "--ignore-uncommitted", out=out)
        report = read_json(f"{out}/report.json") or {}
        source = report.get("source") or {}
        changed = [f.get("path") for f in (report.get("change_set") or {}).get("files") or []]
        self.check("s5.ignore_uncommitted_runs_from_head",
                   source.get("uncommitted_ignored") is True
                   and DIRTY_FILE in (source.get("dirty_paths") or [])
                   and DIRTY_FILE not in changed
                   and (report.get("run_integrity") or {}).get("sandbox_created") is True,
                   exit=code, run_id=report.get("run_id"), final_outcome=report.get("final_outcome"),
                   source=source, changed_files=changed)

        os.remove(os.path.join(repo, DIRTY_FILE))
        out = self.out("untrusted")
        code, tail = self.dca(repo, "--trust", "untrusted", "--task", task_arg, out=out)
        report = read_json(f"{out}/report.json") or {}
        self.check("s5.untrusted_ineligible_exit_11",
                   code == 11 and report.get("final_outcome") == "blocked"
                   and (report.get("run_integrity") or {}).get("sandbox_created") is False
                   and (report.get("source") or {}).get("bundle_sha256") is None,
                   exit=code, primary_reason=report.get("primary_reason"),
                   run_integrity=report.get("run_integrity"))

        head = git(repo, "rev-parse", "HEAD")
        git(repo, "tag", "t090")
        git(repo, "update-ref", "refs/remotes/origin/main", head)
        for name, ref in (("raw_sha", head), ("tag", "t090"), ("remote_ref", "refs/remotes/origin/main")):
            out = self.out(f"ref-{name}")
            code, tail = self.dca(repo, "--trust", "trusted", "--task", task_arg, "--ref", ref, out=out)
            self.check(f"s5.ref_{name}_exit_3", code == 3 and not os.path.exists(f"{out}/report.json"),
                       exit=code, tail=tail[-400:])
        git(repo, "checkout", "--detach", "HEAD")
        out = self.out("ref-detached")
        code, tail = self.dca(repo, "--trust", "trusted", "--task", task_arg, out=out)
        self.check("s5.detached_head_exit_3", code == 3 and not os.path.exists(f"{out}/report.json"),
                   exit=code, tail=tail[-400:])
        git(repo, "checkout", "main")

    # --- quickstart §6 -------------------------------------------------------------------------

    def approvals(self):
        repo, task, verify = seed("S8", self.work)
        task_arg = f"@{task}"
        base = ("--trust", "trusted", "--task", task_arg, "--verify", verify)
        runs_dir = os.path.join(self.work, ".dca-runs")

        # 1. blocked, at the DEFAULT output location
        code, tail = self.dca(repo, *base)
        origin_dir = max((os.path.join(runs_dir, d) for d in os.listdir(runs_dir)),
                         key=os.path.getmtime) if os.path.isdir(runs_dir) else ""
        origin = read_json(os.path.join(origin_dir, "report.json")) or {}
        requests = [a for a in origin.get("approvals") or [] if a.get("action_class") == 15]
        self.check("s6.first_run_blocked_with_request",
                   code == 11 and origin.get("final_outcome") == "blocked" and bool(requests),
                   exit=code, run_id=origin.get("run_id"), primary_reason=origin.get("primary_reason"),
                   approvals=origin.get("approvals"))
        if not requests:
            return
        request_id = request_id_of(requests[0])

        # 2. stale approvals: each is refused before a sandbox exists
        stale = {
            "task_changed": (repo, ("--trust", "trusted", "--task", "Different task text.",
                                    "--verify", verify)),
            "trust_changed": (repo, ("--trust", "untrusted", "--task", task_arg, "--verify", verify)),
        }
        for name, (where, args) in stale.items():
            code, tail = self.dca(where, *args, "--approve", request_id, out=self.out(f"stale-{name}"))
            self.check(f"s6.stale_{name}_exit_3", code == 3, exit=code, tail=tail[-400:])
        other = "codex" if self.backend == "claude" else "claude"
        command = [DCA, "run", "--repo", repo, "--backend", other, *base, "--approve", request_id,
                   "--out", self.out("stale-backend_changed")]
        done = subprocess.run(command, capture_output=True, text=True, timeout=LIVE_TIMEOUT)
        self.check("s6.stale_backend_changed_exit_3", done.returncode == 3, exit=done.returncode,
                   tail=(done.stdout + done.stderr)[-400:])
        git(repo, "-c", "user.name=t090", "-c", "user.email=t090@invalid", "commit", "--allow-empty",
            "-m", "t090 stale commit")
        code, tail = self.dca(repo, *base, "--approve", request_id, out=self.out("stale-commit_changed"))
        self.check("s6.stale_commit_changed_exit_3", code == 3, exit=code, tail=tail[-400:])
        git(repo, "reset", "--hard", "HEAD~1")

        # 3. the approve re-run: default report location, grant with provenance, used once
        out = self.out("approved")
        code, tail = self.dca(repo, *base, "--approve", request_id, out=out)
        self.grant_checks("s6.approve_rerun", code, out, request_id, origin)

        # 4. a run without --approve is blocked again (grants do not persist); custom --out origin
        out = self.out("third")
        code, tail = self.dca(repo, *base, out=out)
        third = read_json(f"{out}/report.json") or {}
        again = [a for a in third.get("approvals") or [] if a.get("action_class") == 15]
        self.check("s6.third_run_without_approve_blocked",
                   code == 11 and third.get("final_outcome") == "blocked" and bool(again),
                   exit=code, run_id=third.get("run_id"), approvals=third.get("approvals"))
        if not again:
            return
        third_id = request_id_of(again[0])

        # 5. a custom --out origin: --approve alone cannot find it; --approval-report can
        code, tail = self.dca(repo, *base, "--approve", third_id, out=self.out("no-report-path"))
        self.check("s6.custom_out_without_approval_report_exit_3", code == 3, exit=code,
                   tail=tail[-400:])
        out = self.out("approved-custom")
        code, tail = self.dca(repo, *base, "--approve", third_id,
                              "--approval-report", f"{self.out('third')}/report.json", out=out)
        self.grant_checks("s6.approve_with_approval_report", code, out, third_id, third)

    def grant_checks(self, check_id, code, out, request_id, origin):
        report = read_json(f"{out}/report.json") or {}
        grants = (read_json(f"{out}/grants.json") or {}).get("grants") or []
        grant = next((g for g in grants if g.get("request_id") == request_id), {})
        provenance = grant.get("provenance") or {}
        expected = {"origin_run_id": origin.get("run_id"),
                    "task_fingerprint": origin.get("task_fingerprint"),
                    "source_commit": (origin.get("source") or {}).get("commit"),
                    "origin_backend": origin.get("backend"),
                    "origin_trust_level": origin.get("trust_level")}
        self.check(f"{check_id}.grant_provenance",
                   all(provenance.get(k) == v for k, v in expected.items())
                   and str(provenance.get("origin_report_digest", "")).startswith("sha256:"),
                   provenance=provenance)
        unanswered = [a for a in report.get("approvals") or [] if a.get("action_class") == 15]
        self.check(f"{check_id}.succeeded", code == 0 and report.get("final_outcome") == "succeeded"
                   and not unanswered,
                   exit=code, run_id=report.get("run_id"), final_outcome=report.get("final_outcome"),
                   primary_reason=report.get("primary_reason"), approvals=report.get("approvals"))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--backend", action="append", choices=BACKENDS, required=True)
    options = parser.parse_args()
    stamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H-%M-%SZ")
    results = {"task": "T090", "started": stamp, "backends": {}}
    for backend in (b for b in BACKENDS if b in options.backend):
        work = os.path.join(ROOT, "benchmark", "work", f"T090-{backend}-{stamp}")
        os.makedirs(work)
        validation = Validation(backend, work)
        validation.preconditions()
        validation.approvals()
        results["backends"][backend] = {"work": os.path.relpath(work, ROOT),
                                        "checks": validation.checks}
    failed = [c["id"] for b in results["backends"].values() for c in b["checks"]
              if c["status"] != "pass"]
    results["status"] = "fail" if failed else "pass"
    results["failed"] = failed
    path = os.path.join(ROOT, "benchmark", "results",
                        f"{stamp[:10]}-launcher-approval-validation.json")
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(results, handle, indent=2, sort_keys=True)
        handle.write("\n")
    print(f"{results['status']}: {path}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
