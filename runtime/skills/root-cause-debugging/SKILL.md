---
name: root-cause-debugging
description: Reproduce, isolate, then make the smallest safe fix - with a baseline taken before any change so pre-existing failures are never mistaken for regressions.
---

# Root-cause debugging

A fix you cannot explain is a coincidence. Work in one direction only: reproduce the failure,
isolate the cause, then change the smallest thing that removes it.

## 0. Baseline first, before you change anything

Run the relevant checks **before** making any change and record their results as the baseline.

Run them after the Context Record is written, not before. A baseline run is a verification command,
and a verification command counts as the first workspace mutation, so the Context Record
(`/run/dca/out/context.json`, see the verification skill) has to exist first. The order is: Context
Record, baseline, change. Report the baseline under `verification.baseline` in the receipt.

This is what separates "my change broke this" from "this was already broken", and there is no way
to recover the distinction afterwards. Without a baseline, a pre-existing failure looks exactly like
a regression you introduced, and a regression you introduced looks exactly like a pre-existing
failure.

Record, for each baseline check: what was run, and whether it passed, failed or could not run.
A check that already failed before your change is reported as a pre-existing failure, never as
something your change caused - and never as something your change fixed unless you can show it.

## 1. Reproduce

Get a deterministic reproduction before theorising. Narrow it until it is small and repeatable: the
single failing test, the single input, the single call.

If you cannot reproduce it, say so. A fix for a failure you never saw is a guess dressed as a
change, and it belongs in the report as a risk, not in the diff.

## 2. Isolate

Find the mechanism, not the symptom.

- Follow the actual data: what value arrives, what was expected, where they first differ.
- Bisect the path - by input, by code path, by commit - until one place is responsible.
- State the cause in one sentence: "X happens because Y, at `path:line`." If you cannot write that
  sentence, you have not isolated it yet.
- Distinguish the cause from the trigger. The thing that made it visible is usually not the thing
  that is wrong.

## 3. Smallest safe fix

Fix the cause you isolated, and only that.

- Prefer the change that makes the invalid state impossible over the one that handles it later.
- Do not add defensive code around a cause you have already removed.
- Do not refactor on the way through; record it as a risk or a follow-up instead.
- If the smallest correct fix is large - a contract change, an architectural change - stop, escalate
  the classification to planned, and plan it.

## 4. Prove it

- Re-run the reproduction: it must now pass.
- Re-run the baseline checks: everything that passed before must still pass. A check that changes
  from pass to fail is a regression you caused, and it blocks the change.
- A check that failed in the baseline and still fails is reported as pre-existing, with evidence.
- Add or extend a test that fails without the fix and passes with it. A fix with no such test has
  no evidence that it addressed anything.

## Anti-patterns

- Changing something, seeing green, and inferring the cause backwards.
- Widening a test's tolerance, skipping it, or asserting less so it passes.
- "Fixing" several plausible causes at once - you then know that one of them mattered, but not
  which.
