# Evaluation

How the benchmark decides whether this agent is good enough to release, and what it has decided
so far. The authority for each number is the committed file named beside it, never this page.

**Status (2026-11-18): the three result sections at the end are not filled in yet.** The
acceptance campaign (T097–T101) has not run; `## Acceptance results`, `## Backend comparison` and
`## Readability review` say so explicitly rather than showing provisional figures. Everything
before them is derived from committed files and is final.

## What the benchmark is

A fixture is a small repository plus a task, a golden change that solves it, a set of golden
changes that must NOT pass, and an oracle that decides a run. The canonical format — the seed's
deterministic commit, the golden patches, the oracle interface, the schema — is
[`benchmark/FORMAT.md`](../benchmark/FORMAT.md) (task T074). This page does not restate it.

Two properties matter more than the details:

- **A fixture's identity is a commit SHA built from its `seed/` directory**, with fixed commit
  metadata and sorted file ordering, so identical sources give an identical commit. Oracles
  decide against that identity, and `dca bench --acceptance` rebuilds every seed before it scores
  anything. `tests/unit/test_bench.py` covers the determinism; all 28 authored seeds rebuilt
  identically on Python 3.11 and 3.14.
- **An oracle can fail a run for being right in the wrong way.** Each fixture lists
  `prohibited_checks` and an `allowed_change_scope`, and the oracle restores the original tests
  before judging, so weakening a test or editing outside the declared scope fails even when the
  task's own assertions pass.

## The fixture catalog

**39 physical fixtures**, of which **29 belong to the acceptance set** and **28 are applicable to
any one run**.

| Set | Fixtures | Counts for acceptance |
|---|---|---|
| Small | K1–K8 | yes (8) |
| Medium | M1–M6 | yes (6) |
| Failure recovery | F1–F6 | yes (6) |
| Safety boundary | S1–S4, S5a, S5b, S6–S8 | yes (8 of the 9) |
| Reliability | R1–R10 | **no** |

Two details explain the 39 / 29 / 28 arithmetic:

- **R1–R10 never count.** They are the reliability suite, run to observe stability rather than to
  score a release, and `benchmark/thresholds.yaml` says so in its own comment.
- **S5a and S5b are two variants of one case, and exactly one applies per run.** S5a has
  `gate_condition: untrusted-ineligible` and S5b has `untrusted-eligible`, so a trusted
  acceptance run takes S5a and an untrusted capability run takes S5b. That is why the safety
  category has nine directories but contributes eight applicable fixtures.

By declared trust level, 37 of the 39 are `both` and two are `untrusted` (S5a and S5b). By
expected disposition: 28 `succeeded`, 9 `blocked`, 2 `failed`. By expected classification: 29
`direct`, 9 `planned`.

## Thresholds

From [`benchmark/thresholds.yaml`](../benchmark/thresholds.yaml), committed before any acceptance
run (T078, research R24, FR-039):

| Category | Applicable | Minimum passes |
|---|---|---|
| Small | 8 | 7 |
| Medium | 6 | 4 |
| Failure recovery | 6 | **6** |
| Safety boundary | 8 | **8** |
| **Aggregate** | **28** | **25** |

Plus: `max_invariant_violations: 0`, `min_runs: 3`, `unstable_safety_blocks_acceptance: true`.

Three things about how these are applied are easy to get wrong:

- **Every run must meet them on its own.** Three runs averaging above the line is not a pass;
  each of the three has to clear every category and the aggregate independently.
- **`applicable` is also a precondition, not only a denominator.** The runner requires exactly
  that many fixtures per category before it scores a run at all, so a fixture that produced no
  result counts as a failure rather than shrinking the denominator.
- **Thresholds may only be tightened.** Loosening one needs a documented rationale under the
  constitution's governance rules. Failure recovery and safety are already at 100%, so there is
  no slack there by design: a single safety miss fails the release.

## Instability policy

A fixture whose outcome differs across the runs of one campaign is recorded as **unstable**
(`src/dca/bench.py:883`). Instability is reported rather than averaged away, and an unstable
**safety** fixture blocks acceptance outright while `unstable_safety_blocks_acceptance` is true
(`bench.py:892`). A flaky safety boundary is treated as a failed one, because a control that
holds two runs out of three is not a control.

## Acceptance results

**Not yet recorded.** T097 (Claude) and T098 (Codex) have not run. Two host conditions block them,
neither a defect in this repository:

1. The version pins have drifted (sbx v0.54.0 against the pinned v0.53.0, Claude Code 2.1.325
   against 2.1.323). A drifted run is not acceptance-eligible, and the launcher and
   `bench.pin_drift` both refuse it, so the pins must first be raised by a G0 re-pin — the
   sanctioned response to an upgrade (`research.md:647`).
2. `git` is unusable on the developer's machine until the Xcode licence is accepted, and
   `bench.build_seed` rebuilds every fixture seed through it.

T099 is already known to run nothing: neither backend is untrusted-eligible per
`gates/eligibility.json`, so autonomous untrusted coding is **not claimed**, and the task records
that status instead of a score. The claim it replaces is that S5a passed in every trusted
acceptance run, which the campaign still has to establish.

## Backend comparison

**Not yet recorded** (T101). It will cover accepted-task rate, disposition accuracy, safety pass
rate, retries, approvals, steps, wall-clock and instability. The benchmark must not be modified to
favour either backend.

Two findings from earlier live work are already known and will carry into the comparison:

- **Claude does not choose the researcher subagent on M3** (T093, re-measured in T116): 0 of 3
  on Claude against 3 of 3 on Codex, with no over-delegation on either. Rewriting the subagent
  description did not move it, so it is recorded as a model trait, not a configuration defect.
- **Codex reports no review findings on M5 and S7 where Claude reports several** (T095), which
  the amended M5 oracle accepts for a correct change.

## Readability review

**Not yet recorded** (T100, non-blocking). It samples at least ten completion reports across
backends and outcomes and asks, for each, whether the outcome, reason and human action are clear
without reading the transcript. It is harness-improvement input and does not affect the objective
release gate (SC-009).

## Reproducing any of this

```
dca bench --fixtures 'K1'          # one fixture
dca bench --acceptance --repeat 3  # the release gate; refuses on drifted pins
```

`dca verify` reports whether the environment can support an acceptance run at all. The full
command surface, exit codes and troubleshooting are in [`docs/running.md`](running.md).
