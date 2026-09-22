# DCA reliability baseline 2026-09-22

The 005-dca-hardening reliability campaign: the 10-fixture suite (K1-K8, M1, M2), trusted, one run per fixture per backend, every run a real `bin/dca run`.

Composed from the generated `benchmark/results/<bench-id>/benchmark.json` files, which Git ignores. The machine-readable form is [`reliability-2026-09-22.json`](reliability-2026-09-22.json).

## Original full campaign

The accepted evidence: one full suite per backend. Claude ran at `27d1fd6`. Codex ran at `f5e06ae`, after the scratch-directory policy fix (`2ffaf02`). Every run passed, and no sandbox was left behind.

| Backend | Bench id | DCA commit | Runs | Passed | Failed | Blocked | Mean (s) | Median (s) | Cleanup failures | Class mismatches |
|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|
| claude | `bench-2026-09-22T03-40-11Z-b233` | `27d1fd6` | 10 | 10 | 0 | 0 | 99.0 | 94.6 | 0 | 2 |
| codex | `bench-2026-09-22T15-54-17Z-152c` | `f5e06ae` | 10 | 10 | 0 | 0 | 118.0 | 107.1 | 0 | 2 |

- `bench-2026-09-22T15-54-17Z-152c`: `dca_tree_clean` is false because the result directories of the two earlier runs were untracked in `benchmark/results/`, which alone makes the flag false. The `.gitignore` rule added with this baseline removes that condition.

| Backend | Fixture | Result | Outcome | Duration (s) | Class (expected) | Review | Oracle | Cleanup |
|---|---|---|---|---:|---|---|---|---|
| claude | K1 | passed | succeeded | 98.5 | direct (direct) | - | pass | ok |
| claude | K2 | passed | succeeded | 90.7 | direct (direct) | - | pass | ok |
| claude | K3 | passed | succeeded | 89.3 | direct (direct) | - | pass | ok |
| claude | K4 | passed | succeeded | 84.0 | direct (direct) | - | pass | ok |
| claude | K5 | passed | succeeded | 108.7 | direct (direct) | - | pass | ok |
| claude | K6 | passed | succeeded | 105.3 | direct (direct) | - | pass | ok |
| claude | K7 | passed | succeeded | 88.9 | direct (direct) | - | pass | ok |
| claude | K8 | passed | succeeded | 86.3 | direct (direct) | - | pass | ok |
| claude | M1 | passed | succeeded | 126.3 | direct (planned) | - | pass | ok |
| claude | M2 | passed | succeeded | 112.4 | direct (planned) | - | pass | ok |
| codex | K1 | passed | succeeded | 97.1 | direct (direct) | - | pass | ok |
| codex | K2 | passed | succeeded | 100.3 | direct (direct) | - | pass | ok |
| codex | K3 | passed | succeeded | 108.7 | direct (direct) | - | pass | ok |
| codex | K4 | passed | succeeded | 105.4 | direct (direct) | - | pass | ok |
| codex | K5 | passed | succeeded | 103.8 | direct (direct) | - | pass | ok |
| codex | K6 | passed | succeeded | 124.5 | direct (direct) | - | pass | ok |
| codex | K7 | passed | succeeded | 157.7 | direct (direct) | - | pass | ok |
| codex | K8 | passed | succeeded | 98.2 | direct (direct) | - | pass | ok |
| codex | M1 | passed | succeeded | 145.4 | direct (planned) | - | pass | ok |
| codex | M2 | passed | succeeded | 138.9 | direct (planned) | - | pass | ok |

## Superseded run

| Backend | Bench id | DCA commit | Runs | Passed | Failed | Blocked | Mean (s) | Median (s) | Cleanup failures | Class mismatches |
|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|
| codex | `bench-2026-09-22T03-57-30Z-e2c1` | `27d1fd6` | 10 | 0 | 0 | 10 | 57.6 | 55.5 | 0 | 0 |

- `bench-2026-09-22T03-57-30Z-e2c1`: every Codex run ended `blocked` (dca exit 11, primary reason "the agent produced no valid completion report"). `2ffaf02` then stopped the native deny rules blocking the run scratch directory, and `624a1a0` kept the agent's stderr as redacted run evidence. The Codex rerun at `f5e06ae` passed 10/10. This run is kept only as history.

## Notes

- M1 and M2 expect `planned`, but both backends classified them `direct` in the campaign. Classification mismatches are counted, not scored (tests/unit/test_bench.py test_61), so they did not affect pass/fail.
