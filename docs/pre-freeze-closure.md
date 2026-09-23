# Pre-Freeze Closure

Branch `007-dca-pre-freeze-closure`, from `main` at v0.2.0 (`07886bb`), 2026-09-22. This records
the disposition of every known open finding before T074–T108 continue.

## Current baseline

| Area | State |
|---|---|
| Claude trusted | T062 production conformance PASS (current), T073 PASS, real end-to-end runs |
| Codex trusted | T062 PASS, T073 PASS, real end-to-end runs |
| Reliability benchmark | Claude 10/10, Codex 10/10, 0 cleanup failures ([baseline](../benchmark/baselines/reliability-2026-09-22.md)) |
| Developer experience | `dca init`, local config in `.git/dca/`, `dca run "task"`, `dca verify`; real Claude and Codex runs through the installed `dca` |
| Security | untrusted execution blocked; global network policy default-deny and equal to G4's fingerprint; Codex always `--safety strict` |

## Fixed in 007

| Finding | Resolution | Evidence |
|---|---|---|
| First-mutation detector counted Codex `create_directory` on `/run/dca/out` as a workspace mutation: the scratch exemption ignored the `paths` list. This breaks the active T039 contract, which says writes confined to `/run/dca/out/` don't count. | `paths` lists are read. A list that reaches outside the scratch dir still counts. | `tests/unit/test_events.py` 69a, 69b |
| The detector classed redirected writes (`cat a > b`, `cat <<EOF > file`) and writing arguments (`sort -o`, `find -delete/-exec`, `uniq in out`, `tree -o`, `git diff --output`, `rg --pre`, `file -C`) as read-only. That breaks T039, and the same flag drives host retry accounting (FR-023), so a heredoc repair cycle was never counted as a retry. | They count as mutations. `/dev/null` and descriptor duplication stay inspection. Host-only `events.py`; the kit's `shellparse.py` is untouched, so conformance evidence stays valid. | `test_events.py` 52d, 69c–69f |
| The launcher's Codex sign-in hint named `docker agent login chatgpt`, which the pinned Docker Agent v1.136.0 doesn't have. | `docker agent setup` (select ChatGPT), matching the README, the quickstart and `dca verify`. | `test_developer_experience.py` 90 |
| A one-word task starting with `-` is read as an option. | The standard `--` separator is documented in `dca run --help` and the README. Multi-word tasks already worked. | `test_developer_experience.py` 91, 92 |
| README table: markdownlint MD060. | Spaced delimiter row. | editor diagnostics clean |
| `dca verify` latency, noted in 006 as "tens of seconds". | Measured: 3.7 s live, 0.5 s static. The 006 note was an unmeasured assumption; no change is needed. | measurement, 2026-09-22 |
| T076 was checked while its Evidence ("T075 passes") and acceptance mode had no owner. | Reopened: the reliability runner is done, acceptance mode is open. T096 now depends on it. | `tasks.md` |
| Evidence files `benchmark/results/<date>-*.json` vs the git-ignored `benchmark/results/<bench-id>/`. | Evidence convention stated once (committed summaries). | `tasks.md` Phase 7, quickstart §8 |
| Quickstart §4–§6 weren't executable: no `benchmark/work/K1`, `task.txt` files that don't exist, no `--verify`, `git checkout` of an untracked file. | Tested fixture-prep snippet, `--verify`, `rm`. | quickstart §4 executed as written |
| Quickstart §1 stale: `gpt-5.6` (G3 recorded the `gpt-5.5` fallback), no install, no pinned Docker Agent binary. | Rows corrected and added. | `gates/G3.json`, quickstart §1 |
| T074/T079 wording named files that don't exist (`build_seed.py`, `apply_golden.py`, `test_bench_tools.py`, `repo.bundle`); T106 described the pre-006 CLI. | Wording synchronized. | `tasks.md` |

## Fixture IDs (resolved)

005 committed its 10-fixture **reliability suite** under `K1`–`K8`, `M1` and `M2`, the same IDs
T079/T080/T082 specify for different acceptance fixtures. Resolved in 007 by renaming the
reliability suite. Nothing changed but the ID: the seeds build to the same commits, and every other
file is byte-identical.

| Historical | Current | | Historical | Current |
| --- | --- | --- | --- | --- |
| K1 | R1 | | K6 | R6 |
| K2 | R2 | | K7 | R7 |
| K3 | R3 | | K8 | R8 |
| K4 | R4 | | M1 | R9 |
| K5 | R5 | | M2 | R10 |

- **Future runs** report `R1`–`R10`. The 2026-09-22 baseline and the ignored run artifacts keep the
  IDs they were recorded with; the baseline carries this mapping.
- **Reserved:** `K*`, `M*`, `F*` and `S*` belong to the acceptance fixtures. None exists yet;
  T079, T080, T082, T084, T085, T087–T089, T092 and T094 create each one as specified.
- **Counting:** the acceptance matrix (T075) counts the K/M/F/S definitions only, never `R*`.
- **Still to cover:** the acceptance requirements the reliability suite doesn't cover (FR-019,
  FR-014a, FR-012, FR-013, FR-009, FR-001b, docs plus a test) are what T079, T080 and T082 exist to
  deliver.

## Deferred to T074–T108

| Finding | Owner task | Reason |
|---|---|---|
| FR-001 generic ordering check, and detector precision: the read-only allowlist lacks `echo`/`printf` and over-reports early mutations in the fail-closed direction. | T075 (test), T076 (acceptance mode) | Only the acceptance FR-001 check consumes ordering. |
| Classification mismatches are counted, not scored (SC-010). | T075/T076; T080 (plan-free K fixture) | Acceptance scoring. |
| The Claude full-suite evidence predates `2ffaf02` (campaign at `27d1fd6`). Post-fix Claude reruns cover M1, M2 and K7 only (now R9, R10, R7). | T081, T097 | Acceptance re-runs every fixture on both backends. |

## Deferred post-freeze

| Finding | Reason | Risk |
|---|---|---|
| `dca verify` regroups `verify.sh` by parsing its `verify: FAIL <check>:` lines. | Tested and working; a structured output mode would be a new interface. | Low: an unknown check id is shown under "Other checks", never hidden. |
| Benchmark baseline tables use compact `\|---\|` delimiters. | Cosmetic, generated text. | None. |

## Won't fix in V1

| Finding | Reason |
|---|---|
| Host environment drift: Claude Code auto-updating past its pin, disk cleanups resetting the global sbx policy, Docker Desktop auto-starting unrelated containers. | Outside DCA. DCA detects it (`dca verify` pins and fingerprint, exit 3 preconditions) and never changes global settings. |

## T074–T108 readiness

| Status | Tasks |
|---|---|
| Already satisfied | T074, T077 (005). T076's reliability runner (005) |
| **Next** | **T075**, the acceptance-matrix tests; then T076 acceptance mode, then T078 |
| Ready (dependencies met) | T075, T079, T080, T082, T084, T085, T087, T088, T089, T092, T094, T102, T103, T104, T106, T107 |
| Wording updated in 007 | T074, T075, T076, T079, T096, T106; quickstart §1, §4–§6, §8 (used by T081, T090, T108) |
| Blocked by prior tasks | T076 (T075), T078 (T075), T081 (T079, T080), T083 (T082), T086 (T084, T085), T090 (T089), T091 (T087–T089), T093 (T092), T095 (T089, T094), T096–T101 (the acceptance chain), T105 (T100, T101), T108 (T102–T107) |
| Superseded | none |

Ownership:
- The FR-001 ordering check belongs to T075/T076.
- The detector itself belongs to T039/T040, which are complete and were fixed in 007.

No task assumes an architecture that no longer exists, and no decision is open. The next
implementation task is **T075**.
