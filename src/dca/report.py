"""Completion report: merge, finalization, rendering (tasks.md T044).

Standard library only. The report is the single artifact a developer reads to decide whether to
trust a change, so the one rule that governs this module is constitution I: **the launcher computes
`final_outcome` and never takes it from the agent unchecked.** The agent's claim is an input. It
survives only where it agrees with host-side evidence, and every difference is recorded in
`outcome_overrides` with the rule that fired, so the disagreement is visible rather than silently
resolved.

The decision order below is research D-FIN / FR-035a, in that order and not another one. Order
matters because several rules can apply at once and they do not commute:

  1. **no usable agent report → `blocked`.** Nothing downstream can be evaluated, so nothing
     downstream is guessed.
  2. **run integrity → `blocked`.** A malformed or truncated stream, or an abnormal agent exit,
     means the host cannot say what happened. A host-triggered stop is different: the host knows
     exactly what it did, so it goes on to rule 4 (FR-023a).
  3. **`none-adequate` verification → `blocked`, with an empty change set.** The agent declared no
     adequate way to verify; files changed anyway would be an unverifiable diff, recorded as a
     safety event.
  4. **`succeeded`** needs every required check passing on the FINAL state, at least one required
     check, every acceptance criterion satisfied, and - at a limit - proof that the evidence
     predates the limit. Planned tasks additionally need the plan and an independent review that
     saw an unchanged candidate.
  5. **otherwise FR-035a decides `failed` vs `blocked`**: a check that conclusively failed is a
     `failed` task; a check that could not run, or an approval that was never answered, is
     `blocked`, because "we could not tell" is not "it does not work".

WHY A `fail` OUTRANKS A MISSING REVIEW. A planned task whose required check conclusively failed
before any limit is `failed` even when the review is missing: the more specific rule wins, because
telling the developer "blocked, no review" would hide the fact that their tests do not pass.
"""

import json
import os

_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.dirname(os.path.dirname(_HERE))

try:
    from . import jsonschema
    from .errors import OUTCOME_EXIT
except ImportError:  # loaded by path in tests and in the sandbox
    import importlib.util as _ilu
    import sys as _sys

    def _sideload(name, filename):
        module = _sys.modules.get(name)
        if module is None:
            spec = _ilu.spec_from_file_location(name, os.path.join(_HERE, filename))
            module = _ilu.module_from_spec(spec)
            _sys.modules[name] = module
            spec.loader.exec_module(module)
        return module

    jsonschema = _sideload("dca_jsonschema", "jsonschema.py")
    OUTCOME_EXIT = _sideload("dca_errors", "errors.py").OUTCOME_EXIT

SCHEMA_VERSION = "1.2"
SCHEMA_PATH = os.path.join(_REPO_ROOT, "specs", "001-bounded-coding-agent", "contracts",
                           "completion-report.schema.json")

SUCCEEDED = "succeeded"
FAILED = "failed"
BLOCKED = "blocked"
MISSING = "missing"

REVIEWER_MISMATCH = "reviewer-fingerprint-mismatch"
UNVERIFIED_CHANGES = "changes-without-adequate-verification"

CHECK_FIELDS = ("id", "command_or_method", "required", "executed_by", "after_last_change",
                "result", "exit_status", "output_ref")


def load_schema(path=None):
    with open(path or SCHEMA_PATH, encoding="utf-8") as handle:
        return json.load(handle)


# --- what the agent said (untrusted) --------------------------------------------------------------


def _is_agent_report_usable(agent):
    """Can anything in the agent's report be evaluated at all? (D-FIN rule 1)

    This is deliberately about STRUCTURE, not about whether the claim is believable. An agent that
    claims success it did not earn is handled by rules 2-5; an agent whose report cannot be read is
    handled here, because there is nothing to disagree with.
    """
    if not isinstance(agent, dict):
        return False
    if agent.get("outcome") not in (SUCCEEDED, FAILED, BLOCKED):
        return False
    classification = agent.get("classification")
    if not isinstance(classification, dict) or classification.get("value") not in (
            "direct", "planned"):
        return False
    verification = agent.get("verification")
    if not isinstance(verification, dict) or verification.get("type") not in (
            "deterministic", "alternative", "none-adequate"):
        return False
    return True


def _clean_check(check):
    cleaned = {key: check[key] for key in CHECK_FIELDS if key in check}
    cleaned.setdefault("required", False)
    cleaned.setdefault("executed_by", "agent")
    cleaned.setdefault("after_last_change", False)
    cleaned.setdefault("result", "unresolved")
    cleaned.setdefault("command_or_method", cleaned.get("id", "unnamed"))
    cleaned.setdefault("id", cleaned["command_or_method"])
    return cleaned


def _merge_checks(agent_checks, launcher_checks):
    """Agent evidence, with the launcher's own re-execution overriding it by check id.

    The schema says it plainly: the launcher's final re-execution on the final task-branch state is
    authoritative for deterministic checks. The agent's own run of the same check happened at some
    earlier state, so where both exist, the later authoritative one replaces it.
    """
    merged = []
    index = {}
    for check in agent_checks or []:
        cleaned = _clean_check(check)
        index[cleaned["id"]] = len(merged)
        merged.append(cleaned)
    for check in launcher_checks or []:
        cleaned = _clean_check(dict(check, executed_by="launcher"))
        position = index.get(cleaned["id"])
        if position is None:
            index[cleaned["id"]] = len(merged)
            merged.append(cleaned)
        else:
            merged[position] = cleaned
    return merged


def _clean_criteria(criteria):
    cleaned = []
    for item in criteria or []:
        if not isinstance(item, dict):
            cleaned.append({"text": str(item), "status": "unknown"})
            continue
        entry = {"text": str(item.get("text", "")),
                 "status": item.get("status") if item.get("status") in
                 ("satisfied", "unsatisfied", "unknown") else "unknown"}
        if isinstance(item.get("evidence_ref"), str):
            entry["evidence_ref"] = item["evidence_ref"]
        cleaned.append(entry)
    return cleaned


def _clean_review(review):
    if not isinstance(review, dict):
        return None
    cleaned = {}
    for key in ("performed", "identical"):
        if isinstance(review.get(key), bool):
            cleaned[key] = review[key]
    for key in ("fingerprint_before", "fingerprint_after"):
        if isinstance(review.get(key), str):
            cleaned[key] = review[key]
    cleaned["evidence_origin"] = "vm"
    findings = []
    for finding in review.get("findings") or []:
        if not isinstance(finding, dict):
            continue
        findings.append({
            "category": str(finding.get("category", "unspecified")),
            "severity": str(finding.get("severity", "unspecified")),
            "evidence": str(finding.get("evidence", "")),
            "status": finding.get("status") if finding.get("status") in ("open", "resolved")
            else "open",
        })
    if findings:
        cleaned["findings"] = findings
    return cleaned


# --- the decision ---------------------------------------------------------------------------------


def verification_satisfied(verification):
    """Every required check `pass` on the FINAL state, and there is at least one required check.

    `after_last_change = false` means the evidence is stale, and stale evidence is not evidence: a
    check that passed before the last edit says nothing about what the developer would merge.
    """
    if not isinstance(verification, dict):
        return False, "no verification evidence was recorded"
    if verification.get("type") not in ("deterministic", "alternative"):
        return False, f"verification type is {verification.get('type')!r}"
    checks = verification.get("checks") or []
    required = [check for check in checks if check.get("required")]
    if not required:
        return False, "no required check was recorded"
    for check in required:
        if check.get("result") != "pass":
            return False, (f"required check {check.get('id')!r} is "
                           f"{check.get('result')!r}, not pass")
        if not check.get("after_last_change"):
            return False, f"required check {check.get('id')!r} is stale (ran before the last change)"
    return True, "every required check passes on the final state"


def _criteria_satisfied(criteria):
    if not criteria:
        return False, "no acceptance criterion was recorded"
    for item in criteria:
        if item.get("status") != "satisfied":
            return False, f"acceptance criterion {item.get('text')!r} is {item.get('status')}"
    return True, "every acceptance criterion is satisfied"


def _fr035a(verification, approvals, limit_reached):
    """FR-035a: `failed` when something conclusively failed, `blocked` when nothing could tell."""
    checks = (verification or {}).get("checks") or []
    required = [check for check in checks if check.get("required")]

    for check in required:
        if check.get("result") == "fail" and not limit_reached:
            return FAILED, (f"required check {check.get('id')!r} failed before any limit"), None

    for check in required:
        if check.get("result") in ("error", "unresolved") or (
                limit_reached and check.get("result") != "pass"):
            return BLOCKED, (
                f"required check {check.get('id')!r} could not be resolved"
                + (f" before the {limit_reached} limit" if limit_reached else "")), (
                "run the check yourself on the returned branch, then re-run dca with the "
                "information it needs")

    for approval in approvals or []:
        if approval.get("status") in ("denied", "unanswered"):
            return BLOCKED, (
                f"approval {approval.get('id')!r} for action class "
                f"{approval.get('action_class')} was {approval.get('status')}"), (
                f"review the request and re-run with --approve {approval.get('id')}")

    for check in required:
        if check.get("result") == "fail":
            return FAILED, f"required check {check.get('id')!r} failed", None

    if not required:
        return BLOCKED, "no required check was recorded, so nothing proves the change works", (
            "declare a verification command with --verify and re-run")

    for check in required:
        if not check.get("after_last_change"):
            return BLOCKED, f"required check {check.get('id')!r} is stale", (
                "re-run the check on the returned branch")

    # No specific FR-035a rule fired. Returning None hands the reason back to `decide`, which names
    # the condition it actually found unmet - "did not satisfy every condition" names nothing the
    # developer can act on.
    return BLOCKED, None, (
        "read the risks and blockers below, then re-run with what the agent was missing")


def decide(draft, limit_evidence_predates=None):
    """`(final_outcome, primary_reason, human_action_required, overrides, safety_events)`.

    `limit_evidence_predates` is FR-023a, and it is a HOST fact, not a report field: the report's
    checks carry no timestamps, so whether the success evidence existed before the limit fired can
    only be answered by the launcher that watched the stream. `None` means the launcher could not
    establish it, and at a limit that is treated as "no", because FR-023a is a permission to
    succeed, not a default.
    """
    integrity = draft["run_integrity"]
    verification = draft.get("verification")
    review = draft.get("review")
    classification = draft.get("classification")
    limit_reached = draft["limits"].get("limit_reached")
    agent_outcome = draft["agent_outcome"]
    safety = list(draft.get("safety_events") or [])
    overrides = []

    def settle(outcome, reason, action, rule):
        if agent_outcome != outcome:
            overrides.append({"from": agent_outcome, "to": outcome, "rule": rule})
        return outcome, reason, action, overrides, safety

    if isinstance(review, dict) and review.get("identical") is False and REVIEWER_MISMATCH \
            not in safety:
        safety.append(REVIEWER_MISMATCH)

    # 1. No usable agent report.
    if agent_outcome == MISSING:
        return settle(BLOCKED, "the agent produced no valid completion report",
                      "inspect events.jsonl and the returned branch, then re-run the task",
                      "D-FIN.1 missing or invalid agent report")

    # 2. Run integrity.
    if integrity["stream"] in ("malformed", "truncated"):
        return settle(BLOCKED, f"the event stream was {integrity['stream']}",
                      "the run cannot be trusted; re-run the task",
                      "D-FIN.2 run integrity")
    if integrity["agent_exit"] == "abnormal":
        return settle(BLOCKED, "the agent terminated abnormally",
                      "the run cannot be trusted; re-run the task",
                      "D-FIN.2 run integrity")
    if integrity["stream"] == "none":
        return settle(BLOCKED, draft.get("primary_reason")
                      or "the run was blocked before a sandbox was created",
                      draft.get("human_action_required")
                      or "resolve the policy block reported below, then re-run",
                      "D-FIN.2 no sandbox was created")

    # 3. none-adequate verification.
    if isinstance(verification, dict) and verification.get("type") == "none-adequate":
        if draft["change_set"].get("files"):
            safety.append(UNVERIFIED_CHANGES)
        return settle(BLOCKED,
                      "no adequate verification approach could be established for this task",
                      "define a verification command with --verify, or reduce the task to "
                      "something the repository can check",
                      "D-FIN.3 none-adequate verification")

    # 4. Can this be a success?
    verified, verification_reason = verification_satisfied(verification)
    criteria_ok, criteria_reason = _criteria_satisfied(draft.get("acceptance_criteria"))
    planned = isinstance(classification, dict) and classification.get("value") == "planned"

    blockers = []
    if not verified:
        blockers.append(verification_reason)
    if not criteria_ok:
        blockers.append(criteria_reason)
    if limit_reached and limit_evidence_predates is not True:
        blockers.append(
            f"the run stopped at the {limit_reached} limit and the success evidence is not "
            "proven to predate it (FR-023a)")
    if planned:
        if not draft.get("plan_ref"):
            blockers.append("a planned task recorded no plan (FR-008)")
        if not (isinstance(review, dict) and review.get("performed") is True):
            blockers.append("a planned task was not independently reviewed (FR-020)")
        elif review.get("identical") is not True:
            blockers.append("the candidate changed while the reviewer read it (FR-022)")
    if isinstance(review, dict) and review.get("identical") is False:
        blockers.append("the candidate changed while the reviewer read it (FR-022)")

    if not blockers:
        return settle(SUCCEEDED, None, None, "D-FIN.4 success conditions satisfied")

    # 5. FR-035a decides between failed and blocked.
    outcome, reason, action = _fr035a(verification, draft.get("approvals"), limit_reached)
    if outcome == FAILED and (
            isinstance(review, dict) and review.get("identical") is False):
        # A reviewer-immutability violation can never be reported as a plain task failure: it is a
        # safety-invariant violation and the developer has to see it as one.
        outcome, reason, action = BLOCKED, blockers[-1], (
            "re-run the review on an unchanged candidate")
    if outcome == BLOCKED and action is None:
        action = "read the blockers below and re-run with what the agent was missing"
    return settle(outcome, reason or blockers[0], action,
                  "FR-035a" if outcome == FAILED else "D-FIN.5 / FR-035a")


# --- assembly ---------------------------------------------------------------------------------------


def build(run_id, backend, trust_level, task_fingerprint, source, sandbox_settings, analysis,
          agent_report, change_set, limits_configured, versions, launcher_checks=(),
          approvals=(), cost_enforced=False, limit_evidence_predates=None,
          primary_reason=None, human_action_required=None, safety_events=()):
    """Merge host evidence with the agent's claim and finalize. Returns the report dict.

    `analysis` is a `dca.events.RunAnalysis`; everything it contributes - run integrity, counters,
    token usage, the limit that was reached - is host-side and is never read from the agent.
    """
    usable = _is_agent_report_usable(agent_report)
    agent = agent_report if usable else {}
    integrity = analysis.run_integrity()
    provisioned = bool(integrity["sandbox_created"])

    verification = None
    if usable and provisioned:
        raw = agent.get("verification") or {}
        verification = {"type": raw.get("type"),
                        "checks": _merge_checks(raw.get("checks"), launcher_checks)}
        if raw.get("baseline"):
            verification["baseline"] = [_clean_check(c) for c in raw["baseline"]]
        for key in ("alternative_definition", "limitation"):
            if isinstance(raw.get(key), str):
                verification[key] = raw[key]
        if verification["type"] == "alternative":
            verification.setdefault("alternative_definition", "not recorded by the agent")
            verification.setdefault("limitation", "not recorded by the agent")

    classification = None
    if usable and provisioned:
        raw = agent.get("classification") or {}
        classification = {"value": raw.get("value"),
                          "reason": str(raw.get("reason") or "not recorded")}
        if raw.get("escalated_from") == "direct":
            classification["escalated_from"] = "direct"

    limits = {
        "configured": dict(limits_configured or {}),
        "used": analysis.counters(),
        "limit_reached": analysis.limit_reached,
        "native_ceiling": analysis.native_ceiling,
    }

    draft = {
        "schema_version": SCHEMA_VERSION,
        "run_id": run_id,
        "backend": backend,
        "trust_level": trust_level,
        "task_fingerprint": task_fingerprint,
        "run_integrity": integrity,
        "source": dict(source),
        "sandbox_settings": sandbox_settings if provisioned else None,
        "classification": classification,
        "agent_outcome": agent.get("outcome") if usable else MISSING,
        "final_outcome": BLOCKED,
        "outcome_overrides": [],
        "primary_reason": primary_reason,
        "human_action_required": human_action_required,
        "acceptance_criteria": _clean_criteria(agent.get("acceptance_criteria")) if usable else [],
        "change_set": dict(change_set),
        "verification": verification,
        "approvals": [dict(approval) for approval in approvals or []],
        "limits": limits,
        "cost_enforced": bool(cost_enforced),
        "token_usage": analysis.token_usage,
        "risks": [str(item) for item in (agent.get("risks") or [])],
        "blockers": [str(item) for item in (agent.get("blockers") or [])],
        "safety_events": list(safety_events or []),
        "versions": dict(versions),
    }
    if usable and provisioned:
        if isinstance(agent.get("repository_map"), dict):
            draft["repository_map"] = _repository_map(agent["repository_map"])
        draft["plan_ref"] = agent.get("plan_ref") if isinstance(agent.get("plan_ref"), str) else None
        review = _clean_review(agent.get("review"))
        draft["review"] = review

    outcome, reason, action, overrides, safety = decide(draft, limit_evidence_predates)
    draft["final_outcome"] = outcome
    draft["outcome_overrides"] = overrides
    draft["primary_reason"] = reason if outcome != SUCCEEDED else None
    draft["human_action_required"] = action if outcome == BLOCKED else None
    if safety:
        draft["safety_events"] = safety
    elif not draft["safety_events"]:
        draft.pop("safety_events")

    if outcome == BLOCKED and not draft["primary_reason"]:
        draft["primary_reason"] = "the run did not satisfy every condition for success"
    if outcome == BLOCKED and not draft["human_action_required"]:
        draft["human_action_required"] = "read the blockers below and re-run"
    return draft


def _repository_map(raw):
    mapped = {}
    if raw.get("scope") in ("minimal", "component"):
        mapped["scope"] = raw["scope"]
    exploration = raw.get("repo_wide_exploration")
    if isinstance(exploration, dict) and isinstance(exploration.get("performed"), bool):
        entry = {"performed": exploration["performed"]}
        if isinstance(exploration.get("reason"), str):
            entry["reason"] = exploration["reason"]
        mapped["repo_wide_exploration"] = entry
    return mapped


def exit_status(report):
    return OUTCOME_EXIT[report["final_outcome"]]


def validate(report, schema=None):
    """Structural conformance to `contracts/completion-report.schema.json`. Raises on violation."""
    return jsonschema.check(report, schema or load_schema())


# --- rendering --------------------------------------------------------------------------------------


_OUTCOME_HEADLINE = {
    SUCCEEDED: "SUCCEEDED - every required check passes on the final state",
    FAILED: "FAILED - the change does not work",
    BLOCKED: "BLOCKED - the run could not establish whether the change works",
}


def render(report):
    """`report.md`: the same facts as `report.json`, in the order a reviewer needs them.

    The disposition and what to do about it come first, then why the launcher decided it, and only
    then the detail. A reviewer who reads one line should still learn the one thing that matters.
    """
    out = []
    add = out.append
    add(f"# {report['run_id']}")
    add("")
    add(f"**{_OUTCOME_HEADLINE[report['final_outcome']]}**")
    add("")
    if report.get("primary_reason"):
        add(f"- Reason: {report['primary_reason']}")
    if report.get("human_action_required"):
        add(f"- Next step: {report['human_action_required']}")
    add(f"- Backend: `{report['backend']}` ({report['trust_level']})")
    add(f"- Source: `{report['source']['ref']}` at `{report['source']['commit'][:12]}`")
    if report["change_set"].get("branch"):
        add(f"- Change set: `{report['change_set']['branch']}`")
    add("")

    if report.get("outcome_overrides"):
        add("## Outcome overrides")
        add("")
        add("The agent's own claim was not kept. Each line names the rule that replaced it.")
        add("")
        for override in report["outcome_overrides"]:
            add(f"- `{override['from']}` -> `{override['to']}` ({override['rule']})")
        add("")

    if report.get("safety_events"):
        add("## Safety events")
        add("")
        for item in report["safety_events"]:
            add(f"- **{item}**")
        add("")

    add("## Run integrity")
    add("")
    integrity = report["run_integrity"]
    add(f"- stream: `{integrity['stream']}`")
    add(f"- agent exit: `{integrity['agent_exit']}`")
    add(f"- sandbox created: `{str(integrity['sandbox_created']).lower()}`")
    limits = report["limits"]
    add(f"- limit reached: `{limits.get('limit_reached')}`")
    if limits.get("native_ceiling"):
        ceiling = limits["native_ceiling"]
        add(f"- native ceiling: `{ceiling['event']}` at `{ceiling['config_path']}`")
    used = limits.get("used") or {}
    if used:
        add("- counters: " + ", ".join(f"{name} {value}" for name, value in sorted(used.items())))
    add("")

    verification = report.get("verification")
    add("## Verification")
    add("")
    if not verification:
        add("No verification evidence was recorded.")
    else:
        add(f"Approach: `{verification['type']}`")
        if verification.get("limitation"):
            add(f"Limitation: {verification['limitation']}")
        add("")
        add("| check | required | ran | state | result |")
        add("|---|---|---|---|---|")
        for check in verification.get("checks") or []:
            add("| `{id}` | {required} | {by} | {state} | {result} |".format(
                id=check.get("id"),
                required="yes" if check.get("required") else "no",
                by=check.get("executed_by"),
                state="final" if check.get("after_last_change") else "stale",
                result=check.get("result")))
    add("")

    add("## Acceptance criteria")
    add("")
    if not report["acceptance_criteria"]:
        add("None recorded.")
    for item in report["acceptance_criteria"]:
        add(f"- [{'x' if item['status'] == 'satisfied' else ' '}] {item['text']} "
            f"({item['status']})")
    add("")

    add("## Change set")
    add("")
    files = report["change_set"].get("files") or []
    if not files:
        add("No files were changed.")
    for item in files:
        add(f"- `{item['path']}` ({item['status']})")
    add("")

    if report.get("approvals"):
        add("## Approvals")
        add("")
        for approval in report["approvals"]:
            add(f"- `{approval['id']}` class {approval['action_class']} "
                f"`{approval['normalized_target']}` -> **{approval['status']}**")
        add("")

    for heading, key in (("Risks", "risks"), ("Blockers", "blockers")):
        if report.get(key):
            add(f"## {heading}")
            add("")
            for item in report[key]:
                add(f"- {item}")
            add("")

    add("## Versions")
    add("")
    for name, value in sorted(report["versions"].items()):
        add(f"- {name}: `{value}`")
    add("")
    return "\n".join(out)


def write(report, out_dir):
    """Write `report.json` and `report.md`. Returns both paths."""
    os.makedirs(out_dir, exist_ok=True)
    json_path = os.path.join(out_dir, "report.json")
    markdown_path = os.path.join(out_dir, "report.md")
    with open(json_path, "w", encoding="utf-8") as handle:
        json.dump(report, handle, indent=2, sort_keys=True, ensure_ascii=False)
        handle.write("\n")
    with open(markdown_path, "w", encoding="utf-8") as handle:
        handle.write(render(report))
    return json_path, markdown_path
