---
name: verification
description: Choose and record the verification approach in the Context Record before the first workspace mutation; deterministic checks first, model confidence never counts, stale evidence is unresolved.
---

# Verification

The run can only be reported `succeeded` on evidence. Decide what that evidence will be **before**
you change anything - deciding afterwards means choosing the check that happens to pass.

## 1. Deterministic checks first

Look for a check the **repository already establishes**: its test command, lint, typecheck, build,
or the command its CI runs. Use that. It is what the developer already trusts, and it is what the
launcher will re-execute on the final state.

Prefer, in order: the repository's own declared command → the test that covers the changed
behavior → a narrower check that still exercises it. Do not invent a new checking mechanism when
one exists.

Mark as `required` every check that must pass for the task to be done. A check nothing depends on
is not verification.

## 2. If no deterministic check exists

Then the approach is `alternative`, and you must document **before the first workspace mutation**:

- `definition`: exactly how the change will be shown to work - the command, comparison or
  observation, stated precisely enough that someone else could repeat it;
- `limitation`: what this does not prove, in the form "deterministic verification for the affected
  behavior was unavailable, so ...".

An alternative approach chosen after the edit is not an approach, it is a rationalization.

## 3. If neither can be established

The approach is `none-adequate`. The task is **blocked**:

- write the report with that outcome and reason;
- **modify no file at all**. A change nobody can check is worse than no change, because it looks
  like progress.

## 4. Record it before the first workspace mutation

Write the approach into `/run/dca/out/context.json` before the first tool call that can change the
workspace - and that includes **before the first build or verification command**, since those can
rewrite the tree themselves. Writes confined to `/run/dca/out/` are not mutations, so recording the
approach is always possible first.

```json
"verification_approach": {"type": "deterministic|alternative",
                          "checks": [{"id": "...", "command_or_method": "...", "required": true}],
                          "definition": "...", "limitation": "..."}
```

## 5. Model confidence is never verification

"This looks correct", "the change is straightforward", "the logic is equivalent" and "I reviewed it
carefully" are not evidence. Reading your own diff is not running the check. The only things that
count are an executed check with its result, or - for `alternative` - the documented observation
actually carried out.

## 6. Stale evidence is unresolved

A check's evidence is valid only for the state it ran against. **Any** later change to the
workspace invalidates it. A check that passed before your last edit is `unresolved`, exactly as if
it had never run - and it blocks success in the same way.

So: make the change, then run the checks. If you touch anything afterwards, run them again. The
launcher re-executes required deterministic checks on the final state and uses its own result, so
stale agent-side evidence is not merely ignored, it is contradicted.

## Results vocabulary

`pass`, `fail`, `error` (could not complete), `partial`, `unresolved` (not executed, timed out, or
stale). Report the result you observed, including the exit status where you have it. Never report
`pass` for a check you did not run to completion.
