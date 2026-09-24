# Data Model: docker-coding-agent-v1

**Source**: spec.md Key Entities + research.md decisions. All persisted artifacts are files
(YAML/JSON). The runtime has no database.

## Entities

### Task
The developer's bounded request, given to `dca run`.

| Field | Type | Rules |
|---|---|---|
| `id` | string | launcher-generated `run-<UTC timestamp>-<6 hex>` |
| `prompt` | string | required, non-empty |
| `acceptance_criteria` | list[string] | optional; if absent, the agent derives criteria and records them in the report (FR-014a) |
| `repo` | path | a git repository on the host; it is **never mounted** into the VM |
| `ref` | local branch ref | Defaults to `HEAD`. Must resolve to exactly one **local branch**: a branch name, `refs/heads/<branch>`, or `HEAD` **only while attached to a local branch** (then the branch's full ref is used). Refused at preflight (exit 3): a tag, a raw commit SHA, a revision expression (`HEAD~1`, `main^`), a remote-tracking ref, an ambiguous name, or a detached `HEAD`. The launcher records the fully qualified `refs/heads/*` ref in `source.ref` and the branch's commit SHA in `source.commit`. Only that committed state is delivered, as a git bundle created **from the branch ref** (research R11). Untracked and ignored files never reach the VM. Tags and arbitrary commits are V1.1 candidates; V1 creates no temporary host refs |
| `uncommitted_policy` | enum `refuse`\|`ignore` | default `refuse`: a dirty checkout (uncommitted or untracked non-ignored changes) stops the run with exit 3. `ignore` requires `--ignore-uncommitted` and is recorded in the report with the dirty path names |
| `source_bundle_sha256` | string \| null | SHA-256 of the source bundle **once it exists**. It is `null` before the bundle is created, and it stays `null` in the report of a run blocked before provisioning (`sandbox_created = false`). If bundle creation or validation fails, the run is infrastructure-aborted (exit 4, no report) |
| `task_fingerprint` | string | `sha256:<hex>`, computed on the host by the single helper defined in **Task fingerprint algorithm** below. The same helper is used for the originating run and for approval re-runs. It identifies "the same task" for approval provenance |
| `trust_level` | enum `trusted`\|`untrusted` | **default `untrusted`** (FR-029a) |
| `backend` | enum `claude`\|`codex` | default `claude`; fixed for the whole run (R3) |
| `verification_commands` | list[string] | optional declared commands; otherwise discovered and recorded before first use |
| `classification` | enum `direct`\|`planned` | set by the agent; escalation direct→planned allowed once (FR-009) |

**Task fingerprint algorithm** (one helper, `task_fingerprint()`, used for every run and every approval check):

1. **Inputs**, decoded as UTF-8 (invalid UTF-8 refuses the request with exit 2):
   - `prompt`: the `--task` text, or the exact contents of the `@file`;
   - `acceptance_criteria`: the lines of the `--criteria` file, split on `\n` after step 2. Lines of length 0 are omitted; no other line is changed or dropped. Missing flag → `[]`;
   - `verification_commands`: the `--verify` values in command-line order. Missing flag → `[]`.
2. **Line endings**: replace every `\r\n` with `\n` in every string. **No other normalization**: no trimming, no whitespace collapsing, no Unicode normalization, no case folding.
3. **Object**: exactly the three keys `prompt` (string), `acceptance_criteria` (array of strings), `verification_commands` (array of strings). **Array element order is preserved.**
4. **Serialization**: JSON with keys in sorted order, `,` and `:` separators with no spaces, and non-ASCII characters emitted literally (Python: `json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"))`), encoded as UTF-8.
5. **Digest**: `"sha256:" + hex(SHA-256(serialized bytes))`.

Test vector:
- `prompt = "Fix the off-by-one in  paginate()\r\n"` (double space kept; CRLF becomes LF);
- `acceptance_criteria = ["page 2 starts at item 11", "tests pass"]`;
- `verification_commands = ["make test"]`;
- serialized bytes: `{"acceptance_criteria":["page 2 starts at item 11","tests pass"],"prompt":"Fix the off-by-one in  paginate()\n","verification_commands":["make test"]}`;
- result: `sha256:95a55eec90cdab43c62e2a329c873b1ebf2c19f6384c5b25117d4557287a5086`.

Changing inner whitespace or criteria order changes the fingerprint; CRLF vs LF does not.

### Run
One execution of a Task in one fresh sandbox.

| Field | Type | Rules |
|---|---|---|
| `run_id` | string | = Task.id |
| `backend`, `trust_level` | enums | copied from Task, immutable |
| `sandbox_name` | string | `dca-<run_id>`; removed at end (after retrieval) |
| `limits` | object | the **authoritative host-side limit snapshot**: `runtime/policy/limits.yaml` `host_limits` for the classification in force (switched to `planned` on escalation). Codex `native_ceilings` are static defense in depth and not part of it (research R19) |
| `grants` | list[ApprovalGrant] | from `--approve` only; **authoritative copy on the host**; an in-VM copy feeds the cooperative gate |
| `counters` | {steps, retries, verification_runs, tokens} | **Authoritative**: counted by the host launcher from **typed outer Docker Agent events**, failing closed on a malformed stream or abnormal termination (evidence: G11). The stream is not intrinsically tamper-proof. **Advisory**: the in-VM gate's own counters, used only for a clean early stop. In-VM state is not protected from a process with sudo. A **retry** = one repair/re-verify cycle (a workspace change followed by re-execution of a required check that previously returned non-`pass`) |
| `run_integrity` | {stream: complete\|host-terminated\|malformed\|truncated\|none, agent_exit: normal\|host-limit\|abnormal\|not-started, sandbox_created: boolean} (all three **required**) | Launcher-recorded; `host-terminated` / `host-limit` only when the launcher itself stopped the run. `malformed`, `truncated` or `abnormal` can never yield `succeeded`. **`sandbox_created = false`** (a policy block before provisioning) ⇒ `stream = none`, `agent_exit = not-started`, `sandbox_settings = null`, `source.bundle_sha256 = null`, `classification = null`, `verification = null`, empty change set, `final_outcome = blocked`. **`sandbox_created = true`** ⇒ `bundle_sha256` and `sandbox_settings` are non-null, and `stream`/`agent_exit` are not `none`/`not-started`. The report schema enforces all of this |
| `sandbox_settings` | object \| null | recorded: `mountless: true`, `skills: off`, `ssh_agent_forwarding: false`, network policy digest; `null` only when `sandbox_created = false` |
| `state` | enum | see state machine |
| `versions` | object | docker-agent, claude, sbx, schema, harness commit |

**Run state machine**

```text
created ──preconditions ok──▶ policy check ──eligible──▶ provisioning ──sandbox usable──▶ running ──agent exits──▶ collecting ──retrieved──▶ finalized
   │                              │                         │ (source bundle,                  │                        │ (re-verify,
   │ precondition failure         │ policy says BLOCKED     │  sbx setup)                      │ host limit /           │  task-branch bundle
   │                              │                         │ infrastructure failure           │ abnormal exit /        │  export, copy, verify,
   ▼                              ▼                         ▼                                  │ malformed stream       │  import)
refused                       finalized(blocked)         infra-aborted                        ▼                        │ retrieval failure
(exit 3, no report)           (exit 11; no sandbox)      (exit 4, no report;              collecting                   ▼
                                                          best-effort sbx rm)             (task-level; G11)          infra-aborted
                                                                                                                     (exit 4, no report;
                                                                                                                      no dca/<run-id> branch;
                                                                                                                      best-effort sbx rm)
```

- **Precondition failure** (exit 3; no task disposition, no report): sbx unavailable, version mismatch, backend authentication unavailable, dirty checkout without override, provider API-key contamination, SSH forwarding active, required gate evidence missing, invalid or stale (`gates/eligibility.json`; a common gate such as G4; the selected backend's availability or trusted-profile gates; its final G11 PASS; its production-conformance PASS), the global network-policy fingerprint differing from the one G4 and production conformance recorded, stale or undiscoverable approval.
- **Policy says BLOCKED** (exit 11; blocked report; no sandbox): an untrusted request with no backend satisfying the required security gates; approval required with no grant, or a required safe prerequisite unavailable, when detectable before provisioning. The same outcomes detected during the run also finalize as `blocked` (exit 11).
- **Infrastructure-aborted** (exit 4; **no task disposition, no completion report**; never `succeeded`). It is reached in three ways:
  1. **Source-bundle failure**: creating or validating the source bundle on the host fails, before any sandbox exists.
  2. **Provisioning failure** before the agent starts: `sbx create`, applying the network policy, `sbx cp` of the bundle and run config, the in-VM clone, or the kit/gate preflight fails.
  3. **Retrieval (finalization) failure** after the agent has run: creating, copying, verifying or importing the **task-branch bundle** fails. The system then can't produce a trustworthy, reviewable change set.

  In every case the launcher prints a diagnostic, keeps only safe diagnostic and event artifacts (`events.jsonl`, gate log; no report claiming a disposition), does a **best-effort `sbx rm`**, and **never fetches or creates a partial `dca/<run-id>` branch**. Docker or git infrastructure failure is **never** classified as a task `blocked` outcome.
- **Native ceiling terminations** (Codex, research R19): when the typed stream ends with a Docker Agent `budget_exceeded`, `max_iterations_reached` or `error` event with `code: loop_detected`, the run is classified deterministically as `stream = complete`, `agent_exit = normal`, `limits.limit_reached = native_ceiling`, with the event's structured detail (`config_path` and, where present, `budget`, `limit`, `used`, `max`, `max_iterations`) in `limits.native_ceiling`. It is a bounded-execution task outcome, never an infrastructure abort. It finalizes as `blocked` unless FR-023a allows `succeeded`. Host limits remain the authoritative direct/planned limits.
- **Task-level failures stay task outcomes**: once the agent process has started, an abnormal agent exit, a malformed or truncated event stream, a host-enforced limit, or a native ceiling termination is a task-run outcome. Its evidence path is defined by G11, and it finalizes as `blocked` (exit 11) unless FR-023a allows `succeeded` at a limit. This holds only if the task-branch retrieval then succeeds; otherwise case 3 applies.

`finalized` always produces a Completion Report and removes the sandbox, if one was created. `refused` and `infra-aborted` produce no report. The branch
`dca/<run_id>` is kept in the host repo only if the change set is non-empty. `collecting`
includes the launcher's **final re-verification** of required deterministic checks inside the
VM on the final task-branch state, before the change-set bundle is retrieved.

### Repository Map
Produced by the agent (repository-navigation skill); referenced from the report.

| Field | Type | Rules |
|---|---|---|
| `scope` | enum `minimal`\|`component` | `minimal` for direct, `component` for planned (FR-001a) |
| `target_files`, `related_tests`, `conventions` | lists | required before the first workspace mutation (FR-001), in the Context Record |
| `verification_approach` | VerificationApproach | required before the first workspace mutation, in the Context Record |
| `repo_wide_exploration` | {performed: bool, reason?: string} | reason required if performed (FR-001b) |

### Context Record
The run-scoped record of what root decided **before changing anything**. Root writes it to
`/run/dca/out/context.json` in the run scratch dir (class 6); the launcher copies it out as
`<out>/context.json`. It is **run evidence, not repository content**, and it is VM-originated.
It never grants approvals, never changes host policy or limits, and is never an input to
provenance. The in-VM gate may read its scope set only to classify cooperatively (class 4).

| Field | Type | Rules |
|---|---|---|
| `classification` | {value: direct\|planned, reason: string, escalated_from?: direct} | required (FR-007); `escalated_from` only after the single direct→planned escalation (FR-009), which rewrites the record |
| `repository_map` | {scope, target_files[], related_tests[], conventions[], repo_wide_exploration} | the minimum Repository Map (FR-001, FR-001a, FR-001b) |
| `verification_approach` | {type: deterministic\|alternative, checks[], definition?, limitation?} | `definition` and `limitation` required for `alternative` (FR-014a). `none-adequate` isn't written here: it ends the task `blocked` before any mutation |
| `plan_ref` | string \| null | required for planned tasks: the Plan written to `/run/dca/out/plan.md` before the first workspace mutation (FR-008); `null` for direct tasks |
| `written_at` | timestamp | must precede the first workspace mutation |

**First workspace mutation**: the first tool call that **actually changed** the workspace or
candidate repository state - the first *effective* mutation. A call the policy gate refused before
it ran is an **attempted** mutation: it stays recorded, attributed and reported (`first_attempted_mutation`,
the per-call `mutation` and `denied` flags, and the gate log), but the workspace never saw
it, so the ordering rules do not measure from it. Both points are kept: `first_attempted_mutation`
and `first_effective_mutation`. FR-001 and FR-008 measure from the effective one; everything else
that reads mutations - retry accounting, the reviewer and researcher invariants, safety, approval and
DENY accounting - continues to read attempts, unchanged. `dca bench` honours a refusal read out of a
tool response only when the host's own gate log records that refusal, because a response is payload;
a structural `hook_blocked` event needs no corroboration.

A call counts as a mutation when it can change the workspace or candidate repository state. That
includes file writes, edits, deletes, renames
and directory changes in the workspace, git operations that change the index, refs or worktree,
dependency installation, formatting or regeneration, and any shell command the policy gate
doesn't classify as read-only inspection (build and verification commands included, since they
can change the workspace). **Not** mutations: reads and read-only inspection (file reads,
listing, search, read-only git status/log/diff/show), writes confined to the run scratch dir
`/run/dca/out/` (such as `context.json`, `plan.md` and `report.agent.json`), skill loading, and
delegation to the researcher or reviewer. The Context Record must exist before the first
**effective** workspace mutation; the benchmark checks this from the event stream, corroborated by
the gate log (FR-001).

### Plan (planned tasks only)
`{scope, constraints, steps[], verification_approach, deviations[]}`, written to
`/run/dca/out/plan.md` and referenced by `plan_ref`. Must exist before the first workspace
mutation on a planned task (FR-008); deviations need a reason.

### VerificationApproach / Verification Evidence

| Field | Type | Rules |
|---|---|---|
| `type` | enum `deterministic`\|`alternative`\|`none-adequate` | `deterministic`: a relevant repository-established deterministic check exists and is required (FR-014). `alternative`: none exists, and an adequate alternative approach was documented before the first workspace mutation (FR-014a). `none-adequate`: neither can be established, so the task is `blocked` and **no file may be modified** (FR-001, FR-014a) |
| `definition` | string | required for `alternative`, documented **before** the first workspace mutation (in the Context Record) |
| `checks[]` | {id, command_or_method, required: bool, executed_by: agent\|launcher, started_at, after_last_change: bool, exit_status, result: pass\|fail\|error\|partial\|unresolved, output_ref} | `unresolved` = not executed, timed out, or stale (`after_last_change = false`). Outputs are kept as file references, not inline |
| `baseline` | {checks[], taken_at} | pre-change run when relevant (FR-019) |
| `limitation` | string | required when `type = alternative` ("deterministic verification for the affected behavior was unavailable…") |

**Verification satisfied**: every check with `required = true` has `result = pass` **on the final
workspace state**. For deterministic checks, this is the launcher's final re-execution. For
alternative evidence, it must be recorded after the last change. There must also be at least
one required check, and every acceptance criterion must be `satisfied` with evidence. This is
the only path to `succeeded`.

### Action Classes
Policy data in `runtime/policy/actions.yaml`, evaluated by the policy gate. Decisions: **ALLOW**,
**ASK** (becomes ALLOW only with a matching grant), **DENY**. Both trust levels use the same classes;
trust level changes only the network rows and the isolation profile.

| # | Action class | Decision | Source |
|---|---|---|---|
| 1 | Read workspace file (realpath inside workspace, not excluded-sensitive) | ALLOW | FR-026a |
| 2 | Read excluded-sensitive path (`policy.sensitive_globs`, credential paths) | DENY | FR-026a, FR-030 |
| 3 | Create/modify in-scope source/test/config/docs inside workspace | ALLOW | FR-026a |
| 4 | Write judged unrelated to task scope (outside Repository Map scope set) | ASK | FR-026a |
| 5 | Any read/write whose realpath is outside workspace + run scratch dir (incl. symlink escape) | DENY | FR-026a, FR-033a |
| 6 | Temporary files in run scratch dir | ALLOW | FR-026a |
| 7 | Regenerate generated files via established procedure | ALLOW | FR-026a |
| 8 | Run declared/recorded verification command (build/test/lint/typecheck/format-check/static analysis) | ALLOW | FR-026a |
| 9 | Formatter limited to in-scope files | ALLOW | FR-026a |
| 10 | Delete tracked workspace file required by task | ALLOW | FR-026a |
| 11 | Delete untracked/ignored file not created in this run; bulk delete beyond scope | ASK | FR-026a, FR-033a |
| 12 | Repository script not in established build/verification workflow | ASK | FR-026a |
| 13 | Modify isolated environment beyond workspace (system config, global install inside VM) | ASK | FR-026a, FR-026b |
| 14 | Install repository-declared dependencies (lockfile/manifest) | trusted: ALLOW · untrusted: ASK (network grant) | FR-026b |
| 15 | Add a new dependency | ASK (+ FR-012 justification) | FR-026b |
| 16 | Install onto host | DENY (structurally impossible from the VM) | FR-026b |
| 17 | Source-control read (fetch/clone) | trusted: ALLOW · untrusted: ASK | FR-026b |
| 18 | Source-control write (push to non-protected branch, open PR/issue) | ASK | FR-026b, FR-033a |
| 19 | Force-push / history rewrite of shared or remote branch; protected-branch merge | DENY | FR-033a |
| 20 | History rewrite of the agent's own unpublished task branch | ASK | FR-033a |
| 21 | External API call: a request by a shell HTTP client or language one-liner to a host not in the policy lists, for a task purpose | ASK | FR-026b |
| 22 | Deployment or production-system action | DENY | FR-033, FR-033a |
| 23 | Credential create/modify/rotate/revoke; security-policy change | DENY | FR-033a |
| 24 | Destructive op on disposable resource created in this run | ALLOW | FR-033a |
| 25 | Modify the agent's own policy, limits, instructions, harness config, gate state | DENY | FR-026a, FR-028 |
| 26 | Invoke skill not in runtime allowlist; spawn subagent not in {researcher, reviewer} | DENY | FR-005, FR-028 |
| 27 | Any mutating tool by researcher/reviewer | DENY | FR-022 |
| 28 | Unparseable or ambiguous shell command | ASK | R20 |
| 29 | Any tool call after step limit reached | DENY (+ stop instruction) | FR-023a |
| 30 | Unconstrained general web browsing: a web-search tool, or fetching a non-policy-listed page with a browsing/fetch tool | DENY | FR-026b |
| 31 | Technical documentation retrieval from a policy-listed documentation host | trusted: ALLOW · untrusted: ASK | FR-026b |

**Class 26 and its positive complement** (research R20; no new class). `actions.yaml` class-26
metadata carries an explicit allowlist. A call matching it is **outside** class 26 and is ALLOW:
- **root** delegating to `researcher` or `reviewer`;
- **root** loading one of the four runtime skills (`repository-navigation`, `root-cause-debugging`, `verification`, `change-receipt`) from the **trusted runtime skill source**: `<KIT_DIR>/skills` with its entry in the canonical `<KIT_DIR>/kit-manifest.json` for Codex, and the G1d-proven source (or the G1d fallback's source and hash check) for Claude.

Every other subagent or skill, a skill whose trusted source can't be established (the name alone
is insufficient), a forked-skill run, and any delegation or skill call by the researcher or
reviewer, is class 26 DENY.

Every row has exactly one decision per trust level. The gate's in-VM decisions are **cooperative**
controls. The host-side sbx network policy, credential proxy, limits and change retrieval are the
enforcement boundary (research R8, R20).

### ApprovalRequest
`{id, run_id, action_class, action, normalized_target, equivalence_class?, reason, risk,
trust_level, status: requested|granted|denied|unanswered}`. Id = `apr-<run_id>-<n>`. In
headless V1, a request with no grant is `unanswered`, which means denied (FR-027c). Recorded in
the report (FR-027d), which also carries the run's `task_fingerprint`, `source.commit`, `backend`
and `trust_level`. That makes the host copy of the report the authoritative origin for any
later grant.

### ApprovalGrant
Created **only** by the host launcher, and only in response to `dca run --approve <request-id>`, which is the **single trusted approval channel in V1** (`granted_by = developer-cli`). The launcher creates the new-run grant after validating the authoritative prior report and the provenance. V1 has **no manually authored grant-file input**, and grants never come from repository content. The **host copy is authoritative**: network grants
reach the sbx policy from the host copy, and reported approvals are reconciled against it. The
in-VM copy only feeds the cooperative gate, and forging it can't grant anything that matters.
Schema: [contracts/approval-grant.schema.json](contracts/approval-grant.schema.json).

**Provenance (host-computed, required)**: `origin_run_id`, `origin_report_digest` (SHA-256 of
the host copy of the prior `report.json`), `task_fingerprint`, `source_commit`,
`origin_backend`, `origin_trust_level`, plus the request's `action_class` and
`normalized_target` or `equivalence_class`.

**Checks before a grant is created**: the launcher verifies that:
1. the prior report is located on the host: at the default `<repo>/../.dca-runs/<origin-run-id>/report.json` (the origin run id is encoded in the request id), or at the path given with `--approval-report <path>` when the origin run used a custom `--out`. Its `run_id` matches the origin run id, it validates against the report schema, and its SHA-256 becomes `origin_report_digest`. V1 has no global approval database;
2. the request id exists in that report's `approvals` with status `requested` or `unanswered`;
3. the action class and normalized target or equivalence class match;
4. the new run's `source_commit`, `task_fingerprint`, `trust_level` and `backend` equal the originals.

If the report can't be located, or any check fails, the approval is **stale or undiscoverable**.
The launcher refuses (exit 3) and a new request is needed. Repository content and in-VM state can't produce any provenance field.

Rules:
- valid only for the run it is attached to;
- matches exactly one action or one declared equivalence class (same action class, same targets or destinations);
- can never match a DENY class;
- never persisted as policy.

### Change Set
Commits on the in-VM task branch `dca/<run_id>`. The branch is exported as a git bundle, copied
out with `sbx cp` into a host quarantine directory outside `.git`, validated there in full —
`git bundle verify`, exact advertised identity (one head, SHA equal to the candidate commit, ref
exactly `refs/heads/dca/<run_id>`), and object-level integrity validation in an isolated
repository — and only then fetched into the host repository as the branch `dca/<run_id>` only. There is no checkout and no merge,
and the host working tree is never touched. Fields in the report: `branch`, `base_commit`,
`head_commit`, `files[] {path, status}`. The launcher computes these itself from the retrieved
bundle; it does not trust the agent's list. `base_commit` equals `source.commit`. Importing is
all-or-nothing: the host ref is created only after the complete quarantine validation succeeds
and the fetch completes. `git bundle verify` alone is not sufficient — G5 observed a truncated
bundle passing it — so object-level validation is part of that gate. Any failure in export,
copy, any validation stage, or import is an infrastructure finalization failure (exit 4, no
report, no `dca/<run_id>` branch).

### Review Finding
`{id, category (missing-requirement|regression|edge-case|unsafe|architecture|weakened-test|insufficient-verification), severity, evidence, status: open|resolved}`.
Review record: `{performed: bool, fingerprint_before, fingerprint_after, identical: bool, findings[]}`.
`identical = false` means the candidate changed while the read-only reviewer ran. That is a
**safety-invariant violation** (FR-022), recorded by the launcher in `safety_events` as
`reviewer-fingerprint-mismatch`. Findings are resolved in the final change set or reflected in
the final disposition.

### Completion Report
Schema: [contracts/completion-report.schema.json](contracts/completion-report.schema.json).
- **Agent-authored fields**: outcome claim, classification, reasons, map, plan, evidence refs, risks.
- **Launcher-authored fields**: backend, trust level, versions, change set, gate log (approvals, denials, counters), limits hit, fingerprints, `cost_enforced`, token usage, final outcome.

**Final outcome rule** (research D-FIN, spec FR-016, FR-035a): the launcher computes
`final_outcome` and never takes it from the agent unchecked.
1. The report is missing or invalid → `blocked`.
2. **Run integrity**: `run_integrity.stream` is `malformed` or `truncated`, or `agent_exit` is `abnormal` → `blocked` (never `succeeded`). A host-triggered stop (`host-terminated` / `host-limit`) goes through rule 4.
3. `verification.type = none-adequate` → `blocked`, and the change set must be empty. A non-empty change set is additionally recorded as a safety event.
4. `succeeded` is allowed **only if**:
   - **verification is satisfied** (all required checks pass on the final state, as above);
   - every acceptance criterion is satisfied with evidence;
   - if a limit was hit, all of that evidence existed before the limit (FR-023a);
   - **for `classification.value = planned` only**: `plan_ref` is non-null (FR-008), `review.performed = true` (FR-020) and `review.identical = true` (FR-022). If any of these is absent or false, the outcome can't be `succeeded`; it is `blocked` unless rule 5 already requires `failed`. Direct tasks don't require a plan or a review;
   - **for any classification**: if a review record exists with `review.identical = false`, the outcome can't be `succeeded` (FR-022), and the safety event `reviewer-fingerprint-mismatch` is recorded in `safety_events`. This doesn't make a review mandatory for direct tasks; it applies only when one ran and the candidate changed.
5. Otherwise, FR-035a decides between `blocked` and `failed`:
   - any required check that is `error` or `unresolved` because it can't run, or that remains unresolved when a limit was hit → `blocked`;
   - an approval that was denied or unanswered with no permitted alternative → `blocked`;
   - a required check that conclusively `fail`s before any limit, or evidenced infeasibility → `failed`.

   The agent's claim is kept only when it is consistent with these rules.

Every difference from `agent_outcome` is recorded in `outcome_overrides` with the rule that
fired (constitution I).

### Benchmark Fixture / Benchmark Run / Acceptance Set
- **Fixture**: [contracts/fixture.schema.json](contracts/fixture.schema.json). `gate_condition` (`always` | `untrusted-ineligible` | `untrusted-eligible`) selects which variant applies to a backend, so exactly one of S5a (fail-closed untrusted request, expected `blocked`) and S5b (untrusted egress/G9) runs. Either one satisfies SC-003's untrusted-profile run.
- **Trust resolution** (research R23): a benchmark run has a profile `P` (`dca bench --trust`, default `trusted`). `trust_level: both` fixtures run under `P`; `untrusted` fixtures always run untrusted; `trusted` fixtures are `not-applicable` under `P = untrusted`. In V1 every fixture except S5a/S5b is `both` and S5a/S5b are `untrusted` (schema-enforced), so both trusted acceptance and untrusted capability acceptance have exactly 28 applicable fixtures (small 8, medium 6, failure-recovery 6, safety 8). `P = untrusted` is refused for a backend that isn't `untrusted_eligible`.
- **BenchmarkRun**: `{id, backend, profile, suite_version, thresholds_ref, results[] {fixture_id, applicability: applicable|not-applicable, run_trust_level, pass, expected_disposition, reported_outcome, violations[]}, aggregate, meets_threshold}`. `not-applicable` results count in neither numerator nor denominator; an applicable fixture with no result counts as a failure.
- **AcceptanceSet**: `{runs[≥3], unstable_fixtures[], accepted: bool}`.
