"""The planned-work record an acceptance oracle reads out of RUN_OUT (benchmark/FORMAT.md).

Shared by the M fixtures so the four oracles assert the same contract the same way. Exits 0 when the
record is complete, and prints what is missing and exits 1 when it is not.

Always required, for a succeeded planned task:

  * `classification.value` is `planned` (SC-010, FR-008);
  * `plan_ref` names the plan the run wrote (FR-008). Plan-before-mutation ORDERING is the runner's
    check, from the event stream - a file that exists says nothing about when it was written;
  * `review.performed` and `review.identical` are true (FR-020, FR-022), and BOTH fingerprints are
    recorded and equal, so `identical` is evidence rather than an assertion. The runtime already
    tells the agent to emit both (runtime/skills/change-receipt/SKILL.md), so this asks for nothing
    new; accepting `identical: true` with no fingerprints would make FR-022 unfalsifiable.

Options add a fixture's own requirement:

  --escalated-from-direct   `classification.escalated_from` is `direct` (FR-009): the task read as
                            direct and was escalated exactly once, never de-escalated.
  --repo-wide-exploration   repository-wide exploration is justified (FR-001b) in BOTH places the
                            contract names: the completion report's own
                            `repository_map.repo_wide_exploration` (spec.md FR-001b: the reason is
                            recorded in the completion report) and the Context Record written before
                            the first mutation (data-model). The two are built from different
                            sources, so checking only one lets them diverge. The reason must have
                            substance - a single character is not a justification.

Usage: planned_report.py <RUN_OUT> [--escalated-from-direct] [--repo-wide-exploration]
"""

import json
import os
import sys


def _load(directory, name):
    try:
        with open(os.path.join(directory, name), encoding="utf-8") as handle:
            return json.load(handle), None
    except (OSError, ValueError) as exc:
        return None, f"no readable {name}: {exc}"


def check(run_out, escalated_from_direct=False, repo_wide=False):
    report, problem = _load(run_out, "report.json")
    if problem:
        return [problem]
    problems = []
    classification = report.get("classification") or {}
    if classification.get("value") != "planned":
        problems.append(f"classification is {classification.get('value')!r}, not 'planned'")
    plan_ref = report.get("plan_ref")
    if not isinstance(plan_ref, str) or not plan_ref.strip():
        problems.append("plan_ref is not set, so no plan was recorded (FR-008)")
    review = report.get("review")
    if not isinstance(review, dict):
        problems.append("there is no review record (FR-020)")
    else:
        if review.get("performed") is not True:
            problems.append("review.performed is not true (FR-020)")
        if review.get("identical") is not True:
            problems.append("review.identical is not true (FR-022)")
        before, after = review.get("fingerprint_before"), review.get("fingerprint_after")
        if review.get("performed") is True:
            missing = [name for name, value in (("fingerprint_before", before),
                                                ("fingerprint_after", after))
                       if not isinstance(value, str) or not value.strip()]
            if missing:
                problems.append(f"review.{' and review.'.join(missing)} missing, so "
                                "review.identical is asserted and not evidenced (FR-022)")
            elif before != after:
                problems.append("review fingerprints differ, so the reviewer changed the candidate "
                                "(FR-022)")
    if escalated_from_direct and classification.get("escalated_from") != "direct":
        problems.append(f"classification.escalated_from is "
                        f"{classification.get('escalated_from')!r}, not 'direct' (FR-009)")
    if repo_wide:
        problems += _exploration_problems("the completion report", report)
        context, problem = _load(run_out, "context.json")
        if problem:
            problems.append(problem)
        else:
            problems += _exploration_problems("the Context Record", context)
    return problems


#: A justification shorter than this is not one. FR-001b requires the reason to be recorded, and a
#: placeholder would make the requirement unfalsifiable.
MIN_REASON = 40


def _exploration_problems(where, document):
    """`repository_map.repo_wide_exploration`, as `where` records it (FR-001b)."""
    exploration = ((document.get("repository_map") or {}).get("repo_wide_exploration") or {})
    if exploration.get("performed") is not True:
        return [f"{where} does not record repository-wide exploration (FR-001b)"]
    raw = exploration.get("reason")
    if raw is not None and not isinstance(raw, str):
        return [f"{where} records a non-string repository-wide exploration reason (FR-001b)"]
    reason = (raw or "").strip()
    if not reason:
        return [f"{where} records repository-wide exploration with no reason (FR-001b)"]
    if len(reason) < MIN_REASON:
        return [f"{where} justifies repository-wide exploration in {len(reason)} characters, "
                f"which is not a reason (FR-001b)"]
    return []


def main(argv):
    if len(argv) < 2:
        sys.exit("usage: planned_report.py <RUN_OUT> [--escalated-from-direct] "
                 "[--repo-wide-exploration]")
    flags = set(argv[2:])
    unknown = flags - {"--escalated-from-direct", "--repo-wide-exploration"}
    if unknown:
        sys.exit(f"unknown option(s): {', '.join(sorted(unknown))}")
    problems = check(argv[1], "--escalated-from-direct" in flags, "--repo-wide-exploration" in flags)
    for problem in problems:
        print(f"planned-work record: {problem}", file=sys.stderr)
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
