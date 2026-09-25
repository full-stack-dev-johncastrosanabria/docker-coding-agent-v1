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

  --escalated-from-direct   the run records one real direct->planned escalation (FR-009), in BOTH
                            the report and the Context Record, and the rewritten record carries a
                            `component` scope. Checking only the report would let a run that was
                            planned from the start claim an escalation it never made.
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


#: Tools that write a file, and shells whose command can. Used only to decide whether a tool call
#: WROTE the Context Record, never to judge a command's safety - the gate does that.
WRITER_TOOLS = frozenset({"write", "write_file", "edit", "edit_file", "multiedit", "create_file",
                          "str_replace", "str_replace_editor", "notebookedit", "apply_patch"})
SHELL_TOOLS = frozenset({"bash", "shell", "sh", "run_shell_command", "execute_command", "run"})
CONTEXT_RECORD = "context.json"


def context_record_writes(run_out):
    """How many tool calls wrote the Context Record, or None when there is no event stream.

    An escalation REWRITES the record (root.md), so a run that genuinely escalated wrote it at least
    twice: once as `direct`, once again as `planned` with `escalated_from`. The report and the record
    are both authored by the agent, so requiring them to agree does not raise the cost of inventing
    an escalation - the event stream does, because the host writes it and the agent cannot edit it.

    Counted conservatively: a writer tool aimed at the record, or a shell command that redirects or
    pipes into it. A read of the record is not a write.
    """
    path = os.path.join(run_out, "events.jsonl")
    if not os.path.isfile(path):
        return None
    writes = 0
    try:
        with open(path, encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    event = json.loads(line)
                except ValueError:
                    continue
                if not isinstance(event, dict) or event.get("type") != "tool_call":
                    continue
                call = event.get("tool_call") or {}
                function = call.get("function") or {}
                name = str(function.get("name") or call.get("name") or "").lower()
                arguments = function.get("arguments")
                if arguments is None:
                    arguments = call.get("input")
                text = arguments if isinstance(arguments, str) else json.dumps(arguments or {})
                if CONTEXT_RECORD not in text:
                    continue
                if name in WRITER_TOOLS:
                    writes += 1
                elif name in SHELL_TOOLS and (">" in text or "tee " in text):
                    writes += 1
    except OSError:
        return None
    return writes


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
            elif not all(_looks_like_a_digest(value) for value in (before, after)):
                problems.append("review fingerprints are not digests, so review.identical rests on "
                                "prose rather than evidence (FR-022)")
            elif before != after:
                problems.append("review fingerprints differ, so the reviewer changed the candidate "
                                "(FR-022)")
    if escalated_from_direct:
        if classification.get("escalated_from") != "direct":
            problems.append(f"classification.escalated_from is "
                            f"{classification.get('escalated_from')!r}, not 'direct' (FR-009)")
        # The report is the agent's own summary. The Context Record is a separate artifact, written
        # before the first mutation and retrieved from the VM, and the runtime says an escalation
        # REWRITES it with `escalated_from: direct` and a `component` scope
        # (runtime/skills/repository-navigation). Requiring both to agree means a run cannot claim an
        # escalation it never made just by adding one field to its report.
        context, problem = _load(run_out, "context.json")
        if problem:
            problems.append(problem)
        else:
            record = context.get("classification") or {}
            if record.get("escalated_from") != "direct":
                problems.append("the Context Record does not record the escalation, so the report's "
                                "escalated_from is uncorroborated (FR-009)")
            scope = (context.get("repository_map") or {}).get("scope")
            if scope != "component":
                problems.append(f"the Context Record was not rewritten to a component scope after "
                                f"escalating; scope is {scope!r} (FR-009)")
        writes = context_record_writes(run_out)
        if writes is None:
            problems.append("there is no event stream, so the escalation cannot be corroborated "
                            "(FR-009)")
        elif writes < 2:
            problems.append(f"the event stream shows the Context Record written {writes} time(s); a "
                            "real escalation rewrites it, so this run was planned from the start "
                            "(FR-009)")
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
