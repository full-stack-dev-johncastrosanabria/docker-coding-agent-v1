---
name: repository-navigation
description: Build a proportional Repository Map and the Context Record before the first workspace mutation, retrieving repository detail progressively and only when task-relevant.
---

# Repository navigation

Context is the scarcest resource in the run. A map that is too small makes you edit the wrong file;
a map that is too large crowds out the reasoning that makes the edit correct. Build the smallest
map that makes the change safe, then grow it only where the task actually leads.

## 1. Build a proportional map

The map's scope follows the classification, not your curiosity:

- **direct → `minimal`**: the files the change touches, the tests that cover them, and the
  conventions those files already follow.
- **planned → `component`**: the same, widened to the component boundary the change crosses - its
  public surface, its callers, and the tests that pin its behaviour.

Record, in the Context Record: `target_files`, `related_tests`, `conventions`, and `scope`.

## 2. Retrieve progressively

**Begin from the proportional Repository Map.** It is the starting point for every later read: you
open it first, and everything else follows from a question it raised.

**Read further repository detail progressively, and only when it is task-relevant.** Each new read
answers a specific question the work has already produced - "where is this symbol defined?", "what
does this caller expect?", "what does the existing test assert?" - and you read only as much as
that question needs.

**Never load unrelated repository content wholesale into your primary working context.** Do not
walk the tree reading files "for background", do not pull in whole directories, and do not page an
entire large file into context when one function answers the question. Unrelated content does not
become useful by being present; it displaces the material the change depends on.

Delegate a genuinely broad investigation to the **researcher** instead, and take back its cited
findings rather than the material it read.

## 3. Repository-wide exploration is the exception

Explore repository-wide **only** when the task cannot be scoped without it - for example a
cross-cutting rename, or a behaviour whose location is genuinely unknown after targeted search.
When you do, record it:

```json
"repo_wide_exploration": {"performed": true, "reason": "<why the task could not be scoped without it>"}
```

Without a recorded reason, repository-wide exploration is out of bounds.

## 4. Write the Context Record before the first workspace mutation

Write `/run/dca/out/context.json` **before** the first tool call that could change the workspace -
including before the first build or verification command. Writes confined to `/run/dca/out/` are
not mutations, so recording context is always allowed first.

```json
{"classification": {"value": "direct|planned", "reason": "..."},
 "repository_map": {"scope": "minimal|component", "target_files": [], "related_tests": [],
                    "conventions": [], "repo_wide_exploration": {"performed": false}},
 "verification_approach": {"type": "deterministic|alternative", "checks": [],
                           "definition": "...", "limitation": "..."},
 "plan_ref": null, "written_at": "<timestamp>"}
```

If the classification escalates from direct to planned, rewrite the record with
`escalated_from: "direct"` and a `component` scope.

## Signals you got the scope wrong

- You are reading files that the change will never touch → the map is too wide.
- You are editing a file that is not in `target_files` → re-derive the map before continuing.
- You cannot name the test that would fail if the change were wrong → the map is missing its tests.
