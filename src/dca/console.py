"""Terminal output for `dca run` and `dca verify` (display only; standard library only).

Nothing here decides anything. The run display prints what the launcher reports at its own phase
boundaries, the summary reads the finalized `report.json`, and the verify summary regroups the
named failures `scripts/verify.sh` already produces. Exit statuses, outcomes and check results all
come from those sources unchanged.
"""

import json
import os
import sys
import time

#: The launcher's lifecycle, in order, as `Launcher.progress` reports it.
PHASES = (
    ("preconditions", "Preconditions"),
    ("bundle", "Source bundle"),
    ("sandbox", "Sandbox"),
    ("agent", "Agent"),
    ("verification", "Verification"),
    ("retrieval", "Retrieval"),
    ("cleanup", "Cleanup"),
)
_INDEX = {key: number for number, (key, _) in enumerate(PHASES, 1)}
_LABEL = dict(PHASES)


def duration(seconds):
    seconds = int(round(seconds))
    minutes, seconds = divmod(seconds, 60)
    return f"{minutes}m {seconds:02d}s" if minutes else f"{seconds}s"


class RunDisplay:
    """The `Launcher.progress` observer: one line per reported phase boundary."""

    def __init__(self, stream=None, clock=time.monotonic):
        self.stream = stream
        self.clock = clock
        self.reported = []
        self.agent_started = None

    def _print(self, text=""):
        print(text, file=self.stream or sys.stderr, flush=True)

    def header(self, run_id, rows):
        self._print(f"DCA run {run_id}")
        for label, value, source in rows:
            self._print(f"  {label:<11} {value}" + (f"   ({source})" if source else ""))
        self._print()

    def __call__(self, phase, status, detail=None):
        if phase == "agent":
            if status == "RUNNING":
                self.agent_started = self.clock()
            elif self.agent_started is not None:
                elapsed = duration(self.clock() - self.agent_started)
                detail = f"{detail} · {elapsed}" if detail else elapsed
        self.reported.append((phase, status, detail))
        line = f"  [{_INDEX[phase]}/{len(PHASES)}] {_LABEL[phase]:<14} {status:<8} {detail or ''}"
        self._print(line.rstrip())

    def status(self, phase):
        for key, status, _ in reversed(self.reported):
            if key == phase:
                return status
        return None

    def sandbox_state(self):
        """What cleanup said about the sandbox, in the launcher's own words, or None."""
        for key, status, detail in reversed(self.reported):
            if key == "cleanup":
                return detail if status == "PASS" else f"REMOVAL FAILED: {detail}"
        return None

    def failed_phase(self):
        """The first phase that did not complete: where an exception interrupted the run."""
        completed = {key for key, status, _ in self.reported if status != "RUNNING"}
        for key, label in PHASES:
            if key not in completed:
                return label
        return _LABEL["cleanup"]


def read_report(out_dir):
    try:
        with open(os.path.join(out_dir, "report.json"), encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, ValueError):
        return None


def summary(request, exit_status, display, elapsed, trust_source):
    """The final summary lines. Everything comes from the report the launcher finalized."""
    report = read_report(request.out) or {}
    outcome = report.get("final_outcome") or {0: "succeeded", 10: "failed",
                                              11: "blocked"}.get(exit_status, "unknown")
    lines = ["", f"DCA {outcome.upper()}", ""]
    rows = [("Backend", request.backend), ("Run", request.run_id)]
    if outcome != "succeeded":
        if report.get("primary_reason"):
            rows.append(("Reason", report["primary_reason"]))
        if report.get("human_action_required"):
            rows.append(("Action", report["human_action_required"]))
    checks = [check for check in ((report.get("verification") or {}).get("checks") or [])
              if check.get("executed_by") == "launcher"]
    if checks:
        passed = sum(1 for check in checks if check.get("result") == "pass")
        rows.append(("Verification",
                     f"{'PASS' if passed == len(checks) else 'FAIL'} ({passed}/{len(checks)})"))
    elif report.get("verification") is not None:
        rows.append(("Verification", "none (no verification command was given)"))
    change_set = report.get("change_set") or {}
    if report:
        rows.append(("Files changed", str(len(change_set.get("files") or []))))
        rows.append(("Result", change_set.get("branch") or "no branch (no changes)"))
    report_md = os.path.join(request.out, "report.md")
    if os.path.isfile(report_md):
        rows.append(("Report", report_md))
    sandbox = display.sandbox_state()
    if sandbox is not None:
        rows.append(("Cleanup", sandbox))
    elif display.status("preconditions") == "BLOCKED":
        rows.append(("Cleanup", "no sandbox was created"))
    rows.append(("Duration", duration(elapsed)))
    width = max(len(label) for label, _ in rows)
    lines += [f"  {label:<{width}}  {value}" for label, value in rows]
    if outcome == "blocked" and request.trust == "untrusted":
        lines += ["", untrusted_hint(trust_source)]
    return lines


def untrusted_hint(source):
    how = {"default": "Trust defaulted to untrusted: neither --trust nor the local config set it.",
           "local config": "Trust is untrusted in this checkout's local config.",
           "command line": "Trust was set to untrusted on the command line."}.get(source, "")
    return (f"  {how} DCA V1 runs only trusted profiles; untrusted execution is blocked.\n"
            "  If you trust this repository's content, opt in explicitly for one run with\n"
            "  `--trust trusted`, or for this checkout with `dca init --trust trusted --overwrite`.")


# --- dca verify ---------------------------------------------------------------------------------

VERIFY_CATEGORIES = (
    ("sandboxes", "Docker Sandboxes"),
    ("network", "Network policy"),
    ("pins", "Version pins"),
    ("runtime", "Runtime assets"),
    ("evidence", "Gate evidence"),
    ("environment", "Environment"),
    ("other", "Other checks"),
)

#: scripts/verify_checks.py names every check; this only groups the names for display.
CHECK_CATEGORY = {
    "skills": "runtime", "actions": "runtime", "limits": "runtime", "network": "runtime",
    "agentsignore": "runtime", "spec-kit-isolation": "runtime", "configs": "runtime",
    "tests": "runtime", "eligibility": "evidence", "provider-keys": "environment",
    "pins": "pins", "network-fingerprint": "network",
    "codex-toolsets": "codex", "codex-skills": "codex",
}

ACTIONS = {
    "sandboxes": "Check Docker Sandboxes with `sbx diagnose` (installed, daemon running, signed "
                 "in), remove leftover sandboxes with `sbx rm --force <name>`, then rerun "
                 "`dca verify`.",
    "network": "Inspect `sbx policy ls`. Only on a new Docker Sandboxes installation that reports "
               "an uninitialized policy, run `sbx policy init deny-all`. Any other difference "
               "needs G4 and production conformance re-run (gates/README.md). DCA never changes "
               "global settings.",
    "pins": "Install the versions pinned in runtime/versions.yaml.",
    "runtime": "The DCA checkout's runtime files differ from the committed ones; check "
               "`git status` in the DCA checkout.",
    "evidence": "gates/eligibility.json is missing, invalid or stale; restore it from Git or "
                "re-run the gate review (gates/README.md).",
    "environment": "Unset the named provider API-key variables; DCA uses subscription sign-in "
                   "only.",
    "claude": "Sign in to Claude Code on the host and check it with `claude auth status --text`.",
    "codex": "Sign in for Codex with `docker agent setup` (select ChatGPT).",
    "other": "See `sh scripts/verify.sh` in the DCA checkout for the full output.",
}


def parse_verify_output(text):
    """(failures, notes) from verify.sh's `verify: FAIL <check>: <message>` / `NOTE` lines."""
    failures, notes = [], []
    for line in text.splitlines():
        if line.startswith("verify: FAIL "):
            body = line[len("verify: FAIL "):]
            if body.startswith("("):
                failures.append(("runtime", body))
                continue
            check, _, message = body.partition(": ")
            failures.append((categorize(check, message), message or body))
        elif line.startswith("verify: NOTE "):
            notes.append(line[len("verify: NOTE "):])
    return failures, notes


def categorize(check, message):
    lowered = message.lower()
    if check == "login":
        return "codex" if ("codex" in lowered or "chatgpt" in lowered) else "claude"
    if check == "pins" and "sbx is not installed" in lowered:
        return "sandboxes"
    if check == "network-fingerprint" and ("sbx ls failed" in lowered
                                           or "sandbox(es) exist" in lowered):
        return "sandboxes"
    return CHECK_CATEGORY.get(check, "other")


def verify_lines(rows):
    """rows: (label, status, detail, reasons, action)."""
    lines = ["DCA verify", ""]
    for label, status, detail, reasons, action in rows:
        lines.append(f"  {label:<17} {status:<8} {detail or ''}".rstrip())
        for reason in reasons:
            lines.append(f"  {'':<17} {reason}")
        if action:
            lines.append(f"  {'':<17} Next: {action}")
    return lines
