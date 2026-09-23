---
name: change-receipt
description: Write /run/dca/out/report.agent.json - the agent-authored fields of the completion report - claiming only what the executed evidence supports.
---

# Change receipt

Write `/run/dca/out/report.agent.json` before you finish. It is the only thing you author that the
developer reads, and it is merged into the completion report by the host.

**The host recomputes the outcome.** It counts the tool calls itself, re-executes required
deterministic checks on the final state, computes the change set from the retrieved branch, and
applies the outcome rules to its own evidence. Every difference between your claim and its
conclusion is recorded as an override, with the rule that fired and your original claim beside it.
So an over-claim is never accepted and never invisible - it is simply published as an over-claim.

## Write these fields

```json
{
  "outcome": "succeeded|failed|blocked",
  "classification": {"value": "direct|planned", "reason": "...", "escalated_from": "direct"},
  "repository_map": {"scope": "minimal|component",
                     "repo_wide_exploration": {"performed": false, "reason": "..."}},
  "plan_ref": "/run/dca/out/plan.md",
  "acceptance_criteria": [
    {"text": "...", "status": "satisfied|unsatisfied|unknown", "evidence_ref": "..."}
  ],
  "verification": {
    "type": "deterministic|alternative|none-adequate",
    "alternative_definition": "...",
    "limitation": "...",
    "baseline": [{"id": "...", "command_or_method": "...", "required": true,
                  "executed_by": "agent", "after_last_change": false, "result": "pass",
                  "exit_status": 0}],
    "checks": [{"id": "...", "command_or_method": "...", "required": true,
                "executed_by": "agent", "after_last_change": true, "result": "pass",
                "exit_status": 0, "output_ref": "..."}]
  },
  "review": {"performed": true, "identical": true,
             "fingerprint_before": "...", "fingerprint_after": "...",
             "findings": [{"category": "regression", "severity": "high",
                           "evidence": "path:line", "status": "resolved"}]},
  "risks": ["..."],
  "blockers": ["..."]
}
```

Omit `plan_ref` and `review` on a direct task that had neither. Omit `escalated_from` unless the
single direct→planned escalation actually happened.

## Rules that decide what you may claim

- **`outcome: succeeded`** only when every `required` check has `result: "pass"` **and**
  `after_last_change: true`, there is at least one required check, and every acceptance criterion
  is `satisfied`. On a planned task it additionally needs `plan_ref`, `review.performed: true` and
  `review.identical: true`.
- **`outcome: failed`** when a required check conclusively failed and you know the change does not
  work.
- **`outcome: blocked`** when something could not be established, executed, resolved or approved -
  including `verification.type: "none-adequate"`, for which the change set must be empty.
- **`after_last_change`** is `false` for any check that ran before your last edit. Marking stale
  evidence as fresh is the one error the launcher's own re-execution will contradict directly.
- **`baseline` and `checks` are different lists.** `baseline` holds the runs made before your
  change. `checks` holds only runs made after your last edit, so every required entry in it has
  `after_last_change: true`. Never record a pre-change run in `checks`: a required check that
  predates your last edit is stale, and the host does not report success while one is recorded. The
  launcher's own re-run only replaces a check with the same id, so it does not clear a stale check
  recorded under a different id.
- **`result`** is what you observed: `pass`, `fail`, `error`, `partial`, `unresolved`. Never write
  `pass` for a check you did not run to completion.
- **`review.identical`** is `false` if the candidate's fingerprint changed while the reviewer read
  it. Say so; it is recorded as a safety event either way.

## Risks and blockers

- **risks**: things a reviewer should know that did not stop the work - a pre-existing failure you
  left alone, a behavior you could not cover, something surprising you chose not to change.
- **blockers**: the specific things that stopped you, each concrete enough to act on. "It did not
  work" is not a blocker; "`make test` needs a database the sandbox has no network route to" is.

Keep both honest and short. An empty `risks` list on a non-trivial change is rarely true.
