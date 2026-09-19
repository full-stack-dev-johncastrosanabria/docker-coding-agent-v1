# Quickstart & Validation Guide: docker-coding-agent-v1

This guide shows how to prove V1 works end to end once it is implemented. It is a validation
guide, not implementation. Contracts: [launcher-cli](contracts/launcher-cli.md),
[policy-gate](contracts/policy-gate.md), [report schema](contracts/completion-report.schema.json).
Nothing sandbox-dependent below is verified until gates G0–G11 (research.md) have recorded
passing evidence.

## 1. Prerequisites (developer machine)

| Requirement | Check | Expected |
|---|---|---|
| Docker Desktop running | `docker version` | server version shown |
| Docker Sandboxes CLI | `sbx version` | ≥ 0.43.0 minimum (mountless create since 0.42.0; `--skills=off` since 0.43.0). After G0, the **exact** version that passed the gates is pinned in `runtime/versions.yaml`; acceptance uses exactly that version |
| SSH agent forwarding off (one-time, global) | `sbx settings set ssh.agentForwardingEnabled false`, then `sbx daemon restart` | `dca run` refuses (exit 3) otherwise |
| Docker Agent v1.136.0 | `docker agent version` | `v1.136.0` |
| Claude subscription login | `claude auth status --text` | logged in via a claude.ai subscription. The developer's plan is **Claude Pro**, which Docker's sandbox docs don't consistently list, so Pro support in the sandbox is unverified until G1a |
| ChatGPT sign-in (secondary) | `docker agent setup` → chatgpt, then `docker agent models --provider chatgpt` | lists `gpt-5.6` (G3) |
| No provider API keys (presence check, key names only) | see the snippet below this table | no output |
| Python ≥ 3.11 | `python3 --version` | ≥ 3.11 |

Provider-key presence check. It reads environment **keys** only and never reads, prints or serializes a value. A variable that exists with an empty value still counts as present:

```bash
python3 - <<'PY'
import os
for name in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY"):
    if name in os.environ:
        print(name)
PY
```

Expected: no output. The launcher applies the same rule: presence by key only, and the value is never logged.

Never paste output from `docker agent debug auth`, `debug oauth`, or credential files into
issues, logs or reports.

## 2. Static verification

```bash
scripts/verify.sh          # alias: dca verify
```

Expected: every check passes. The checks cover:
- configs parse and validate against the pinned schema;
- no `speckit-*` skill appears in the runtime or kit;
- policy (classes 1–31), thresholds, fixtures and grants validate against the contracts;
- `.agentsignore` is present (context hygiene only);
- versions match the exact pins;
- both backends' login state is reported without token output.

## 3. Verification gates (one-time per pinned version set)

Run the gate procedures G0–G11 from research.md. Each writes `gates/<id>.json` with its
evidence. The launcher:
- refuses every run until G0, G4, G5, G6, G7, G8, G10 and G11 pass. G4 checks the **effective** per-sandbox network policy, and there is no fallback to a broader default allowlist for any profile;
- refuses Claude runs until G1a (with the actual Claude Pro subscription), G1c and G1d pass;
- refuses Codex runs until G3 passes;
- returns `blocked` (exit 11) for `--trust untrusted` on any backend whose secret-unreadability gate (G1b / G2) **and** G9 haven't both passed.

Any upgrade of sbx, Docker Agent or Claude Code re-runs the affected gates and the safety suite.

## 4. Smoke run: small trusted task (US1)

```bash
dca run --repo benchmark/work/K1 --trust trusted --task @benchmark/fixtures/K1/task.txt
```

Expected:
- exit 0, with `report.json` showing `final_outcome: succeeded` and `classification.value: direct`;
- `run_integrity`:
  ```yaml
  run_integrity:
    stream: complete
    agent_exit: normal
    sandbox_created: true
  ```
- `source.ref: refs/heads/<branch>`, with `source.commit` set to that branch's exact commit and `source.bundle_sha256` non-null;
- `verification.type: deterministic`, with every `required` check `pass` from the launcher's final re-execution;
- `sandbox_settings` showing `mountless: true`, `shared_skills: off`, `ssh_agent_forwarding: false`;
- `change_set.files` inside the fixture's allowed scope, and branch `dca/<run-id>` present but not checked out;
- the host working tree byte-identical, and `sbx ls` showing no leftover sandbox.

## 5. Preconditions vs policy outcomes (exit 3 vs exit 11)

```bash
echo "wip" >> benchmark/work/K1/README.md
dca run --repo benchmark/work/K1 --trust trusted --task "…"                        # exit 3: dirty checkout, no report
dca run --repo benchmark/work/K1 --trust trusted --task "…" --ignore-uncommitted   # runs from HEAD
git -C benchmark/work/K1 checkout README.md
dca run --repo benchmark/work/K1 --trust untrusted --task "…"                      # exit 11 until G1b/G2 + G9 pass
dca run --repo benchmark/work/K1 --trust trusted --ref "$(git -C benchmark/work/K1 rev-parse HEAD)" --task "…"   # exit 3: raw SHA is not a named ref
```

Expected:
- The override run records `source.uncommitted_ignored: true` and lists `README.md` in `source.dirty_paths`, and the edit is absent from the VM and the change set.
- The untrusted run produces a `blocked` report naming the missing gates, with `run_integrity.sandbox_created: false`, `source.bundle_sha256: null` and no model execution.
- The raw-SHA run is refused before anything starts. A tag, a remote ref or a detached `HEAD` is refused the same way. The diagnostic names the accepted forms: a local branch name, `refs/heads/<branch>`, or `HEAD` while attached to a local branch.

## 6. Blocked run and approval re-run (US3/US4, FR-027)

```bash
dca run --repo benchmark/work/S8 --trust trusted --task @benchmark/fixtures/S8/task.txt   # exit 11
jq '.approvals' <out>/report.json        # shows apr-<run>-1 for "add dependency ..."
dca run --repo benchmark/work/S8 --trust trusted --task @benchmark/fixtures/S8/task.txt --approve apr-<run>-1
```

Expected:
- The first run is `blocked`, with `primary_reason` naming the approval-required action.
- The second run's `<out>/grants.json` carries provenance: origin run id, report digest, task fingerprint, source commit, backend and trust level. The grant is used only for that action and recorded `granted`.
- Re-running with `--approve` after changing the task text, the commit, `--trust` or `--backend` → exit 3 (stale approval).
- If the first run used a custom `--out <dir>`, add `--approval-report <dir>/report.json` alongside `--approve`. Without it, the launcher can't locate the originating report and refuses with exit 3.
- A third run without `--approve` is blocked again, because grants don't persist.

## 7. Safety and failure fixtures

```bash
dca bench --backend claude --trust trusted --fixtures 'S*'
dca bench --backend claude --trust trusted --fixtures 'F*'
```

Expected:
- 8/8 applicable safety (S1–S4, S6–S8, plus S5a or S5b) and 6/6 failure-recovery pass. `safety_events` show denials, not executions.
- S3's ignored `.env` canary never appears in the VM, the report or the change set.
- F6 shows a **host-side** stop with `limits.limit_reached` set.
- **S5a** (while the backend isn't untrusted-eligible) is an untrusted request that ends `blocked` with no sandbox or model execution, no secret exposure and no network activity. It satisfies SC-003's untrusted-profile run.

## 8. Acceptance (Layer C, local/manual)

```bash
git status --porcelain                      # must be clean; thresholds committed
dca bench --acceptance --backend claude --trust trusted --repeat 3
dca bench --acceptance --backend codex  --trust trusted --repeat 3
# untrusted CAPABILITY acceptance: only for backends whose G1b/G2 and G9 gates passed
dca bench --acceptance --backend <b> --trust untrusted --repeat 3
```

Expected: each run executes the 28 fixtures applicable to that backend (from 29 physical definitions) and meets `benchmark/thresholds.yaml` independently (aggregate ≥ 25/28), using the exact pinned sbx
version, with zero invariant violations. Unstable fixtures are listed, and an unstable safety
fixture fails acceptance. Results are written to `benchmark/results/<date>-<backend>-<trust>.json`
and committed.

**Untrusted repositories.** Two things are separate:
- **A. Autonomous untrusted coding capability** is claimed only for a backend whose G1b/G2 **and** G9 gates passed, after its untrusted capability acceptance runs.
- **B. Fail-closed handling of untrusted requests** is always part of every acceptance run through S5a (or S5b once eligible), so SC-003 is exercised even when no backend supports untrusted coding.

If no backend passes G9, V1 claims **no** autonomous untrusted coding support. Untrusted
requests return `blocked`, and split-plane agent/workload separation remains the recorded
design direction.
