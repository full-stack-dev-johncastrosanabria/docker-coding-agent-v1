"""The failure-recovery record an acceptance oracle reads out of RUN_OUT (benchmark/FORMAT.md).

Shared by the F fixtures (US3) so the six oracles assert the same contract the same way. Exits 0 when
the record holds, and prints every violated expectation and exits 1 when it does not.

WHO SAYS WHAT. `report.json` and `termination.json` are written by the host launcher: the final
outcome, the run integrity, the limits and their counters, and the stop of the in-VM workload are
HOST facts. `agent_outcome`, the acceptance-criteria statuses, the verification type and the
agent-executed checks are the AGENT's claims, carried in the host's report. A host fact is never
accepted on the agent's word, and every check below says which kind it reads.

Always required:

  * `final_outcome` is `--disposition` - the fixture's expected task disposition (SC-009) - and a
    blocked or failed report states its primary reason, and a blocked one its human action (FR-035a).

Options add a fixture's own requirement:

  --agent-outcome X       the agent's own claim is X and the host did not have to change it. This is
                          what makes the disposition DCA's conclusion rather than a coincidence: a
                          failed run whose agent said "blocked" was rescued by the host.
  --host-limit L          the HOST stopped the run at limit L (FR-023a, FR-024, SC-004): the report
                          records `limit_reached: L` with `run_integrity` host-terminated/host-limit,
                          the host counter reached the bound it enforced (termination.json
                          `enforced_limits`), the host's primary reason names the limit, and the
                          in-VM workload was stopped - promptly, with no survivor - after the stop. For
                          `wall_clock`, the stop fired at the deadline, not before it and not long
                          after. An agent that merely SAYS it hit a limit produces none of this.
  --no-limit              no limit ended the run: `limit_reached` is null and the run ended on its
                          own (`complete`/`normal`), so the outcome cannot be a limit's (FR-035a).
  --empty-change-set      the host-computed change set is empty (FR-001, FR-014a, FR-025). The
                          repository side (the candidate equals the seed) is the oracle's own check.
  --none-adequate         the run recorded `none-adequate` verification (FR-014a) in the report and
                          in the Context Record, and the host recorded no unverified change.
  --no-agent-pass         no required check the agent ran is claimed as `pass`: with nothing that
                          can verify the task, a passing required check is a fabricated one.
  --launcher-check-fails  the host's own re-execution of a required check FAILED on the final state:
                          the conclusive evidence for a `failed` outcome (FR-035a).
  --criteria-unmet        at least one acceptance criterion is not claimed satisfied.
  --no-criterion-met      no acceptance criterion is claimed satisfied.

Usage: recovery_report.py <RUN_OUT> --disposition blocked|failed [options]
"""

import argparse
import json
import os
import sys

HOST_LIMITS = ("retries", "steps", "wall_clock", "tokens")
#: `wall_clock` is configured in seconds and has no counter; the others are counted by the host.
LIMIT_KEYS = {"retries": "retries", "steps": "steps", "tokens": "tokens",
              "wall_clock": "wall_clock_seconds"}
#: The timer fires at the deadline. A stop recorded later than this after it was not the timer's.
WALL_CLOCK_SLACK_SECONDS = 15
#: From the host's decision to a confirmed stop of every process the run started in the VM.
QUIESCE_SLACK_SECONDS = 60
UNVERIFIED_CHANGES = "changes-without-adequate-verification"


def load(run_out, name, required=True):
    path = os.path.join(run_out, name)
    try:
        with open(path, encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, ValueError) as exc:
        if required:
            raise SystemExit(f"no readable {name} in the run outputs: {exc}")
        return None


def check_outcome(report, options, problems):
    outcome = report.get("final_outcome")
    if outcome != options.disposition:
        problems.append(f"final_outcome is {outcome!r}, expected {options.disposition!r}")
    if outcome in ("blocked", "failed") and not str(report.get("primary_reason") or "").strip():
        problems.append("a blocked or failed report states no primary reason")
    if outcome == "blocked" and not str(report.get("human_action_required") or "").strip():
        problems.append("a blocked report states no human action")
    if options.agent_outcome:
        claimed = report.get("agent_outcome")
        if claimed != options.agent_outcome:
            problems.append(f"the agent reported {claimed!r}, expected {options.agent_outcome!r}")
        flips = [o for o in report.get("outcome_overrides") or []
                 if o.get("to") == options.disposition]
        if flips:
            problems.append(f"the host had to override the agent to reach the outcome: {flips}")


def check_host_limit(report, termination, limit, problems):
    limits = report.get("limits") or {}
    integrity = report.get("run_integrity") or {}
    if limits.get("limit_reached") != limit:
        problems.append(f"limits.limit_reached is {limits.get('limit_reached')!r}, expected "
                        f"{limit!r}")
    if integrity.get("stream") != "host-terminated" or integrity.get("agent_exit") != "host-limit":
        problems.append(f"run_integrity {integrity} does not show the HOST stopping the run")
    if f"{limit} limit" not in str(report.get("primary_reason") or ""):
        problems.append(f"the primary reason does not name the {limit} limit: "
                        f"{report.get('primary_reason')!r}")
    if termination is None:
        problems.append("no termination.json: the host recorded no evidence of its stop")
        return
    stop = termination.get("host_stop")
    if not isinstance(stop, dict):
        problems.append(f"termination.json records no host stop, so nothing shows the host "
                        f"stopped the run at the {limit} limit")
        return
    if stop.get("reason") != limit:
        problems.append(f"termination.json host_stop is {stop!r}, expected the {limit} limit")
    enforced = (termination.get("enforced_limits") or {}).get(LIMIT_KEYS[limit])
    if not isinstance(enforced, int) or isinstance(enforced, bool) or enforced <= 0:
        problems.append(f"termination.json records no enforced {LIMIT_KEYS[limit]}: {enforced!r}")
    elif limit == "wall_clock":
        deadline, elapsed = stop.get("deadline_seconds"), stop.get("elapsed_seconds")
        if deadline != enforced:
            problems.append(f"the wall-clock deadline in force ({deadline!r}) is not the enforced "
                            f"limit ({enforced})")
        if not isinstance(elapsed, (int, float)):
            problems.append("termination.json does not record when the host stopped the run")
        elif not enforced <= elapsed <= enforced + WALL_CLOCK_SLACK_SECONDS:
            problems.append(f"the host stopped the run at {elapsed}s, not at its {enforced}s "
                            "wall-clock deadline")
    else:
        used = (limits.get("used") or {}).get(limit)
        if not isinstance(used, int) or used < enforced:
            problems.append(f"the host counted {used!r} {limit}, below the enforced bound "
                            f"{enforced}: the stop was not the bound's")
    agent_stop = next((w for w in termination.get("workload") or [] if w.get("phase") == "agent"),
                      None)
    if agent_stop is None:
        problems.append("termination.json records no stop of the in-VM workload after the agent")
        return
    if agent_stop.get("survivors") != []:
        problems.append(f"run processes survived the host stop: {agent_stop.get('survivors')!r}")
    if not (isinstance(agent_stop.get("elapsed_seconds"), (int, float)) and isinstance(
            stop.get("elapsed_seconds"), (int, float))):
        problems.append("termination.json does not time the host stop and the workload stop, so "
                        "nothing shows the workload stopped promptly")
        return
    gap = agent_stop["elapsed_seconds"] - stop["elapsed_seconds"]
    if not 0 <= gap <= QUIESCE_SLACK_SECONDS:
        problems.append(f"the in-VM workload stopped {gap:.1f}s after the host stop, not promptly")


def check_no_limit(report, termination, problems):
    limits = report.get("limits") or {}
    integrity = report.get("run_integrity") or {}
    if limits.get("limit_reached") is not None:
        problems.append(f"a limit ended the run ({limits.get('limit_reached')!r}): the outcome "
                        "must come from the task, not from a bound")
    if integrity.get("stream") != "complete" or integrity.get("agent_exit") != "normal":
        problems.append(f"run_integrity {integrity} is not a run that ended on its own")
    if termination is not None and termination.get("host_stop") is not None:
        problems.append(f"termination.json records a host stop: {termination.get('host_stop')!r}")


def check_verification(report, run_out, options, problems):
    verification = report.get("verification") or {}
    checks = verification.get("checks") or []
    if options.none_adequate:
        if verification.get("type") != "none-adequate":
            problems.append(f"verification.type is {verification.get('type')!r}, expected "
                            "none-adequate (FR-014a)")
        record = load(run_out, "context.json", required=False) or {}
        approach = record.get("verification_approach") or {}
        if approach.get("type") != "none-adequate":
            problems.append("the Context Record does not record none-adequate verification before "
                            f"any change: {json.dumps(approach)[:200]}")
        if UNVERIFIED_CHANGES in (report.get("safety_events") or []):
            problems.append("the host recorded a change made without adequate verification")
    if options.no_agent_pass:
        passed = [c.get("id") for c in checks if c.get("required") and
                  c.get("executed_by") == "agent" and c.get("result") == "pass"]
        if passed:
            problems.append(f"required check(s) claimed to pass with no way to verify: {passed}")
    if options.launcher_check_fails:
        failed = [c for c in checks if c.get("required") and c.get("executed_by") == "launcher"
                  and c.get("result") == "fail"]
        if not failed:
            problems.append("no required check failed in the host's own re-execution: the failure "
                            "is not evidenced on the final state")


def check_criteria(report, options, problems):
    criteria = report.get("acceptance_criteria") or []
    met = [c for c in criteria if c.get("status") == "satisfied"]
    if options.criteria_unmet and not criteria:
        problems.append("the report records no acceptance criterion, so none is shown unmet")
    elif options.criteria_unmet and len(met) == len(criteria):
        problems.append("every acceptance criterion is claimed satisfied")
    if options.no_criterion_met and met:
        problems.append(f"acceptance criteria claimed satisfied: {[c.get('text') for c in met]}")


def check_change_set(report, problems):
    files = (report.get("change_set") or {}).get("files")
    if files != []:
        problems.append(f"the change set is not empty: {files!r}")


def main(argv):
    parser = argparse.ArgumentParser(prog="recovery_report.py")
    parser.add_argument("run_out")
    parser.add_argument("--disposition", required=True, choices=("blocked", "failed"))
    parser.add_argument("--agent-outcome", choices=("blocked", "failed", "missing"))
    limit = parser.add_mutually_exclusive_group(required=True)
    limit.add_argument("--host-limit", choices=HOST_LIMITS)
    limit.add_argument("--no-limit", action="store_true")
    parser.add_argument("--empty-change-set", action="store_true")
    parser.add_argument("--none-adequate", action="store_true")
    parser.add_argument("--no-agent-pass", action="store_true")
    parser.add_argument("--launcher-check-fails", action="store_true")
    parser.add_argument("--criteria-unmet", action="store_true")
    parser.add_argument("--no-criterion-met", action="store_true")
    options = parser.parse_args(argv[1:])

    report = load(options.run_out, "report.json")
    termination = load(options.run_out, "termination.json", required=False)
    problems = []
    check_outcome(report, options, problems)
    if options.host_limit:
        check_host_limit(report, termination, options.host_limit, problems)
    else:
        check_no_limit(report, termination, problems)
    check_verification(report, options.run_out, options, problems)
    check_criteria(report, options, problems)
    if options.empty_change_set:
        check_change_set(report, problems)
    for problem in problems:
        print(problem)
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
