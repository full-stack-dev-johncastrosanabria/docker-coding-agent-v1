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

  --escalation-consistent   FR-009 is CONDITIONAL, so no escalation is demanded. If the run records
                            one, it must be true: `escalated_from` is `direct`, the completion report
                            and the retrieved Context Record agree, and the rewritten record carries
                            a planned value and a `component` scope. Planned up front with no
                            `escalated_from` is accepted - that is what a correctly classified task
                            looks like, and requiring the field would require inventing history.

                            A stronger check was tried and removed: counting writes of the Context
                            Record in `events.jsonl`, on the theory that an escalation REWRITES it.
                            A real run disproved it - the agent wrote the record twice for ordinary
                            reasons (a shell heredoc, then a Write that corrected it) with no
                            escalation at all, so the count does not discriminate. Both artifacts
                            read here are agent-authored, so genuine escalation is only PARTIALLY
                            host-verifiable and a determined fabricator cannot be stopped at this
                            layer; that limit is real and is not papered over.
  --repo-wide-exploration   repository-wide exploration is justified (FR-001b) in BOTH places the
                            contract names: the completion report's own
                            `repository_map.repo_wide_exploration` (spec.md FR-001b: the reason is
                            recorded in the completion report) and the Context Record written before
                            the first mutation (data-model). The two are built from different
                            sources, so checking only one lets them diverge. The reason must have
                            substance - a single character is not a justification.

Usage: planned_report.py <RUN_OUT> [--escalation-consistent] [--repo-wide-exploration]
"""

import json
import os
import re
import sys


def _looks_like_a_digest(value):
    """A fingerprint is a hash, not a sentence. `sha256:` prefixes are accepted."""
    text = str(value or "").strip()
    text = text[len("sha256:"):] if text.startswith("sha256:") else text
    return len(text) >= 32 and all(c in "0123456789abcdefABCDEF" for c in text)


def _load(directory, name):
    try:
        with open(os.path.join(directory, name), encoding="utf-8") as handle:
            return json.load(handle), None
    except (OSError, ValueError) as exc:
        return None, f"no readable {name}: {exc}"


def check(run_out, escalation_consistent=False, repo_wide=False):
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
            elif not all(_looks_like_a_digest(value) for value in (before, after)):
                problems.append("review fingerprints are not digests, so review.identical rests on "
                                "prose rather than evidence (FR-022)")
            elif before != after:
                problems.append("review fingerprints differ, so the reviewer changed the candidate "
                                "(FR-022)")
    if escalation_consistent:
        problems += _escalation_problems(run_out, classification)
    if repo_wide:
        problems += _exploration_problems("the completion report", report)
        context, problem = _load(run_out, "context.json")
        if problem:
            problems.append(problem)
        else:
            problems += _exploration_problems("the Context Record", context)
    return problems


def _escalation_problems(run_out, classification):
    """FR-009, checked CONDITIONALLY: a recorded escalation must be real and consistent.

    FR-009 is conditional - "MUST escalate ... WHEN discovered work exceeds the original
    classification" (spec.md), "at most once" (plan.md), "`escalated_from` only AFTER the single
    direct->planned escalation" (data-model.md) - and the runtime is explicit that the field is
    omitted "unless the single direct->planned escalation actually happened"
    (runtime/skills/change-receipt). A task correctly classified planned on the first read never
    escalates, so demanding the artifact unconditionally would demand a fabrication.

    So the field is not required. What IS required is that, if present, it is true:

      * its value is `direct`, the only value the report schema admits;
      * every canonical artifact carrying the classification agrees - the completion report and the
        retrieved Context Record - because the runtime REWRITES the record on escalating
        (runtime/skills/repository-navigation);
      * the rewritten record carries a `component` scope, as that same rule requires;
      * the record does not still claim `direct`, which would contradict the escalation it reports.

    Absence is accepted and says only that no escalation was recorded. Genuine escalation is only
    PARTIALLY host-verifiable: both artifacts are authored by the agent, and the host adopts the
    classification from the agent's own report (`launcher._classification_of`). These checks
    therefore establish CONSISTENCY, not causation, and nothing here should be read as proof that
    newly discovered work drove the transition.
    """
    context, problem = _load(run_out, "context.json")
    if problem:
        # Reported whether or not an escalation was claimed: without the record there is nothing to
        # check a claim against, and a planned run that did not retrieve its Context Record is a
        # problem in its own right. `dca bench` catches this independently for any run that mutated
        # (`bench._context_record_problem`), so this only closes the golden-validation path.
        return [f"{problem}, so the classification cannot be corroborated (FR-009)"]
    record = context.get("classification") or {}
    reported = classification.get("escalated_from")
    recorded = record.get("escalated_from")
    if reported is None and recorded is None:
        return []           # planned up front: nothing was claimed, so there is nothing to disprove
    problems = []
    for where, value in (("the completion report", reported), ("the Context Record", recorded)):
        if value is not None and value != "direct":
            problems.append(f"{where} records escalated_from {value!r}; the only escalation the "
                            "contract allows is from direct (FR-009)")
    if reported is None and recorded is not None:
        problems.append("the Context Record records an escalation the completion report does not, so "
                        "the two artifacts contradict each other (FR-009)")
    elif recorded is None and reported is not None:
        problems.append("escalated_from appears only in the completion report; the Context Record "
                        "was never rewritten, so the escalation is uncorroborated (FR-009)")
    if recorded is not None:
        if record.get("value") != "planned":
            problems.append(f"the Context Record reports an escalation but its classification is "
                            f"{record.get('value')!r}, which contradicts it (FR-009)")
        scope = (context.get("repository_map") or {}).get("scope")
        if scope != "component":
            problems.append(f"the Context Record was not rewritten to a component scope after "
                            f"escalating; scope is {scope!r} (FR-009)")
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
        sys.exit("usage: planned_report.py <RUN_OUT> [--escalation-consistent] "
                 "[--repo-wide-exploration]")
    flags = set(argv[2:])
    unknown = flags - {"--escalation-consistent", "--repo-wide-exploration"}
    if unknown:
        sys.exit(f"unknown option(s): {', '.join(sorted(unknown))}")
    problems = check(argv[1], "--escalation-consistent" in flags, "--repo-wide-exploration" in flags)
    for problem in problems:
        print(f"planned-work record: {problem}", file=sys.stderr)
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
