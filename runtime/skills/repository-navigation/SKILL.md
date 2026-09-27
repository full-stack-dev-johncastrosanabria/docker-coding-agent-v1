---
name: repository-navigation
description: Build a proportional Repository Map and the Context Record before the first workspace mutation, retrieving repository detail progressively and only when task-relevant.
---

# Repository navigation

Context is the scarcest resource in the run. A map that is too small makes you edit the wrong file;
a map that is too large crowds out the reasoning that makes the edit correct. Build the smallest
map that makes the change safe, then grow it only where the task actually leads.

## 1. Build a proportional map

Build a **candidate** map first, decide, then let the scope follow. The classification is answered
from the repository, so the map cannot wait for it: name the target files, find and read the tests
that cover them, and only then classify. What the classification settles is how far the map may
WIDEN from there - never your curiosity:

- **direct → `minimal`**: the files the change touches, the tests that cover them, and the
  conventions those files already follow.
- **planned → `component`**: the same, widened to the component boundary the change crosses - its
  public surface, its callers, and the tests that pin its behavior.

Record, in the Context Record: `target_files`, `related_tests`, `conventions`, and `scope`.

### Earn the classification before you record it

`related_tests` is not bookkeeping - it is where the classification is decided. **A test that imports
what you are about to change is the cheapest possible statement of what else depends on it.** Find
those tests the obvious way (search for the symbol or module you are changing, not just for a
same-named test file) and OPEN them before you classify. One may assert a rule across two
implementations, or pin behaviour the task never mentioned; either makes the work planned, and you
cannot know that from the task text or from the file you were pointed at.

So the order is: target files, then the tests that cover them found and READ (or established not to
exist), then any immediately relevant convention or caller those tests point at, then the criteria
evaluated against what they showed, then the classification - `direct` keeps this minimal map,
`planned` widens it to the component - then the Context Record, then the plan if planned, and only
then the first workspace mutation.

### When there are no tests

A repository with no automated tests is a real repository, and a task may forbid adding any. That is a
finding, not a gap, so record it rather than leaving `related_tests` empty and silent:

```json
"test_discovery": {"performed": true, "result": "none",
                   "evidence": ["searched for importers of the changed module and for a tests/ tree; "
                                "the repository has neither, and the task forbids adding one"]}
```

`result` is `found` or `none`. With `found`, `related_tests` names them and you must have OPENED them:
the host corroborates each named test against the run's event stream and a path you only searched for
does not count. With `none`, `related_tests` stays empty and an alternative verification approach may
be the right one. What is never acceptable is no `test_discovery` at all - that is indistinguishable
from never having looked, which is the failure this exists to catch.

Record the result per criterion, so the claim is checkable rather than asserted:

```json
"classification_basis": {
  "contract_change":     {"applies": false,
                          "evidence": ["api/handlers.py is the only caller of can_publish and passes "
                                       "the same arguments; nothing about its signature moves"]},
  "coordinated_changes": {"applies": true,
                          "evidence": ["tests/test_permissions.py imports can_publish from api/auth.py "
                                       "AND from jobs/scheduled.py and asserts both refuse the same "
                                       "expired token, so the rule lives in two places"]},
  "unclear_root_cause":  {"applies": false,
                          "evidence": ["the failing assertion names the expiry comparison directly"]}
}
```

That example is illustrative only - use your own repository's paths. The rules:

- all three criteria appear, each with an explicit `applies` boolean;
- `direct` requires all three `false`; `planned` requires at least one `true`;
- the classification `reason` and the basis agree;
- evidence names files, tests or callers. "None apply" is a conclusion, not evidence, and a direct
  classification whose `coordinated_changes` evidence cites nothing in the repository is read as
  unbuilt - as is one that names no `related_tests` at all.

The policy gate refuses a record with no basis, a basis silent on a criterion, or a basis that
contradicts its own classification, so this is checked before your first mutation rather than after.

**This is not a licence to widen the map.** Opening the tests that cover your target IS the minimal
map; the repository-wide exception below is unchanged and still needs its recorded reason.

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
cross-cutting rename, or a behavior whose location is genuinely unknown after targeted search.
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
 "classification_basis": {"contract_change": {"applies": false, "evidence": ["..."]},
                          "coordinated_changes": {"applies": false, "evidence": ["..."]},
                          "unclear_root_cause": {"applies": false, "evidence": ["..."]}},
 "repository_map": {"scope": "minimal|component", "target_files": [], "related_tests": [],
                    "conventions": [], "repo_wide_exploration": {"performed": false},
                    "test_discovery": {"performed": true, "result": "found|none", "evidence": []}},
 "verification_approach": {"type": "deterministic|alternative", "checks": [],
                           "definition": "...", "limitation": "..."},
 "plan_ref": null, "written_at": "<timestamp>"}
```

If the classification escalates from direct to planned, rewrite the record with
`escalated_from: "direct"` and a `component` scope. Escalation is for scope that could not have been
known here - not for scope this map would have shown you had you read it.

## Signals you got the scope wrong

- You are reading files that the change will never touch → the map is too wide.
- You are editing a file that is not in `target_files` → re-derive the map before continuing.
- You cannot name the test that would fail if the change were wrong → the map is missing its tests.
