# Contract: `dca` launcher CLI

The launcher is the only user-facing entry point and the **host-side enforcement point**. It:

- selects the backend **before** a run;
- delivers only the sanitized committed source;
- provisions a fresh mountless sandbox;
- enforces run limits from the host;
- retrieves the change set;
- writes the completion report.

It never switches backends mid-run and never modifies the host working tree.

## Commands

### `dca run`

```text
dca run --repo <path> --task <text|@file>
        [--ref <branch>]                    # default: HEAD (must be attached to a local branch); local branch only
        [--backend claude|codex]            # default: claude
        [--trust trusted|untrusted]         # default: untrusted
        [--criteria @file]                  # acceptance criteria, one per line
        [--verify "<cmd>"]...               # declared required verification commands
        [--approve <request-id>]...         # grant for THIS run, bound to a prior report's request
        [--approval-report <path>]          # originating report when it isn't at the default location
        [--ignore-uncommitted]              # explicit override: proceed from --ref despite a dirty checkout
        [--out <dir>]                       # default: <repo>/../.dca-runs/<run-id>/ (outside the repo)
        [--allow-drift]                     # permit version drift (recorded; not acceptance-eligible)
```

The launcher evaluates a request in three phases, in order. A later phase runs only if the
earlier ones pass.

**Phase 1: preconditions.** Any failure **refuses the request with exit 3**: no sandbox is
created, no task disposition is assigned, and no completion report is written. A diagnostic is
printed. These are environment, configuration or input problems, not task outcomes.

1. `--repo` is a git repository, and `--ref` is a **packageable named reference**. This is needed because `git bundle create` refuses a raw commit SHA or a revision expression ("Refusing to create empty bundle", verified with git 2.54.0).
   - **Accepted** (local branches only):
     - a local branch name (`main`);
     - its full ref (`refs/heads/main`);
     - `HEAD` **only while attached to a local branch**, in which case the launcher uses that branch's full ref.
   - **Refused (exit 3, with a diagnostic naming the accepted forms)**:
     - a **tag** (lightweight or annotated);
     - a raw commit SHA;
     - a revision expression (`HEAD~1`, `main^`, `@{upstream}`);
     - a **remote-tracking ref** (`origin/main`, `refs/remotes/*`);
     - an **ambiguous name** (one that resolves to more than one ref, e.g. a branch and a tag with the same name);
     - a **detached `HEAD`**. Git could bundle it as a pseudo-ref named `HEAD`, but V1 requires a stable named ref for the source identity.
   - **Resolution**: the launcher resolves the ref with `git rev-parse --symbolic-full-name` to exactly one `refs/heads/*` result, records it in `source.ref`, and records the branch's commit in `source.commit`. For a branch ref this is the commit the ref points to, so it equals the bundle's ref head.
   - V1 creates no temporary host refs. Tags and arbitrary commits are V1.1 candidates: an annotated tag ref points to a tag object, not to the peeled commit, so comparing the bundle's ref head with `source.commit` is only valid for branches.
2. **Dirty checkout**: uncommitted or untracked non-ignored changes without `--ignore-uncommitted`. With the flag, the dirty path names (never contents) are recorded in `source.dirty_paths`. Ignored files never trigger this check and are never delivered.
3. **Provider API-key contamination**: `ANTHROPIC_API_KEY` or `OPENAI_API_KEY` is present in the launcher's environment, even if empty. The check tests **presence by name only**; values are never read into diagnostics, logs, reports or event files. The diagnostic prints only the variable name.
4. **sbx availability and version**: `sbx` is missing or below 0.43.0 (needed for mountless `sbx create` and `--skills=off`), or its version or any other pinned version differs from `runtime/versions.yaml` without `--allow-drift`.
5. **SSH agent**: sbx `ssh.agentForwardingEnabled` is true, or a fixed agent socket would forward. The launcher runs every `sbx` command with `SSH_AUTH_SOCK` removed. It never changes global sbx settings; it prints the one-time developer command instead.
6. **Backend authentication unavailable**: the login isn't present (safe status fields only). For Claude, the launcher offers `--backend codex` and doesn't switch automatically.
7. **Required gate evidence missing**: a common gate (G0, G4, G5, G6, G7, G8, G10, G11) or a trusted-profile gate for the selected backend hasn't passed for the pinned versions. In particular, **G4 (effective network policy) not passed → no run of any profile proceeds**. Missing *untrusted-eligibility* gates (G1b/G2, G9) aren't a precondition failure; they lead to a Phase 2 `blocked` report.
8. **Stale, invalid or undiscoverable approval**: an `--approve <request-id>` whose originating report can't be located, or which fails provenance verification (see *Approval provenance*).

**Phase 2: policy disposition before provisioning.** The request is valid, but its correct
policy outcome is **BLOCKED**. The launcher **finalizes a `blocked` completion report and
exits 11**, with no sandbox, no model or agent execution, and no network activity for the run
(`run_integrity: {stream: none, agent_exit: not-started, sandbox_created: false}`). Cases:

- **Untrusted execution with no eligible backend**: `--trust untrusted` and the selected backend hasn't passed its secret-unreadability gate (G1b for Claude, G2 for Codex) **and** G9. `primary_reason` names the missing gates.
- **Required safe prerequisite unavailable**: detectable before provisioning, for example a task that requires Git LFS or submodules, which V1 doesn't deliver.

**Phase 3: execution.**

1. `git bundle create <tmp>/src.bundle <source.ref>` from the **named ref**, then `git bundle verify`, then confirm that the bundle's head for `source.ref` equals `source.commit`. **Failure here is an infrastructure abort**: exit 4, no report, and no sandbox is created.
2. `sbx create` of a **mountless** sandbox with `--skills=off` and the V1 kit.
3. Apply the strict network policy for the profile, plus the host-authoritative network grants.
4. `sbx cp` the bundle and run config into the VM; the VM clones the bundle to the workspace.
5. Kit/gate preflight inside the VM (the gate executes and fails closed; versions match). The VM creates the task branch `dca/<run-id>` at `source.commit`. **Any failure in steps 2–5 is an infrastructure abort**: exit 4, no report, best-effort `sbx rm`.
6. `sbx exec docker agent run --exec --json …`. The launcher parses **typed outer Docker Agent events**, counts steps and retries, enforces wall-clock with a host timer, and fails closed on a malformed or truncated stream or an abnormal exit (evidence: G11).
7. Final re-execution of the required deterministic checks on the final state.
8. Retrieval. The task-branch bundle is created in the VM from the named ref `dca/<run-id>`, copied out via `sbx cp` into a host quarantine directory, checked with `git bundle verify`, and **only then** fetched as `dca/<run-id>`. **Any failure in export, copy, verification or import is an infrastructure finalization failure**:
   - exit 4;
   - never `succeeded`;
   - no completion report claiming a task disposition;
   - only safe diagnostic and event artifacts are kept (`events.jsonl`, gate log);
   - best-effort `sbx rm`;
   - **no partial `dca/<run-id>` branch is ever fetched or created**.
9. Report finalization, then `sbx rm`.

Task-run outcomes during execution map to exit codes 0, 10 or 11, provided retrieval (step 8) succeeds. They include approval required with no grant (the gate's `DCA_APPROVAL_REQUIRED`), a required prerequisite or check that can't run, host-enforced limits, and an **abnormal agent exit or malformed/truncated event stream**. These last two stay task-run `blocked` outcomes (exit 11) because G11 defines their evidence path; they are not infrastructure aborts.

### Approval provenance (`--approve`)

`--approve <request-id>` creates a grant **only for the new run**. The launcher first locates the
originating report, using host-side data only:

- **Default location**, for ordinary runs: the request id encodes the originating run id (`apr-<origin-run-id>-<n>`), and the launcher reads `<repo>/../.dca-runs/<origin-run-id>/report.json`.
- **Custom location**: if the originating run used a custom `--out`, the developer passes `--approval-report <path>` together with `--approve <request-id>`. The file's `run_id` must equal the origin run id encoded in the request id. With several `--approve` flags, one `--approval-report` applies to requests from that one origin run; requests from different origin runs must each be resolvable at their default location.
- **Not found**: if the report can't be located, or its `run_id` doesn't match, the launcher refuses with exit 3.

There is no persistent or global approval database in V1: the originating report file on the
host is the only source.

The launcher then computes the SHA-256 of that host report file and verifies that:

1. the report parses and validates against [completion-report.schema.json](completion-report.schema.json), and its SHA-256 is recorded as `origin_report_digest`;
2. `<request-id>` is listed in that report's `approvals` with status `requested` or `unanswered`, and its `action_class` is not a DENY class;
3. the grant's action class and normalized target or equivalence class equal the request's;
4. the new run's `source.commit`, `task_fingerprint`, `trust_level` and `backend` equal the originating report's.

Any mismatch makes the approval **stale**. The launcher refuses with exit 3 and tells the
developer to re-run without `--approve` to obtain a new request. The grant's `provenance`
object ([approval-grant.schema.json](approval-grant.schema.json)) records every verified
field. Repository content and in-VM state can't create or alter provenance.

### `dca verify`

Runs the `scripts/verify.sh` checks. It exits non-zero on any failure and never prints tokens.

### `dca bench`

```text
dca bench [--backend claude|codex] [--trust trusted|untrusted] [--fixtures <glob>]
          [--repeat N]          # default 1; acceptance uses 3
          [--acceptance]        # requires committed thresholds, clean tree, exact pinned versions, passed gates
```

Fixtures are selected per backend by `gate_condition`. Exactly one of S5a (untrusted
fail-closed, expected `blocked`) and S5b (untrusted egress/G9) applies to each backend.

## Exit codes

| Code | Meaning | Report written? |
|---|---|---|
| 0 | Run finalized, `final_outcome = succeeded` | yes |
| 10 | Run finalized, `final_outcome = failed` | yes |
| 11 | Valid request whose correct policy outcome is `blocked`: untrusted execution with no backend satisfying the required gates; approval required with no grant; required safe prerequisite unavailable; a required check that can't run; a host-enforced limit without prior success evidence; malformed/abnormal run. The report lists the human action | yes |
| 3 | Precondition failure before any task disposition: sbx unavailable, incompatible or drifted version, backend authentication unavailable, dirty checkout without override, provider API-key contamination, SSH forwarding active, required gate evidence missing (e.g. G4 effective network policy not passed), stale or invalid approval | no (diagnostic only) |
| 2 | Usage error (invalid arguments) | no |
| 4 | Infrastructure abort: no task disposition and never `succeeded`. Three cases: (a) the **source bundle** can't be created or validated (before any sandbox); (b) **provisioning** fails before the agent starts (`sbx create`, network policy, `sbx cp`, in-VM clone, kit/gate preflight); (c) **retrieval** fails after the agent ran (task-branch bundle export, copy, verify or import), so no trustworthy change set exists. The launcher does best-effort `sbx rm`, keeps only safe diagnostic and event artifacts, and never creates a partial `dca/<run-id>` branch. Never reported as a task `blocked` outcome | no (diagnostic only) |

## Outputs

- `<out>/report.json`: matches [completion-report.schema.json](completion-report.schema.json).
- `<out>/report.md`: a short human summary rendered from the JSON.
- `<out>/grants.json`: the host-authoritative grants for this run, if any.
- `<out>/gate.log.jsonl`: the in-VM policy-gate decision log, copied out. It is VM-originated, cooperative evidence and contains no secret values.
- `<out>/events.jsonl`: the host-captured typed outer Docker Agent events, which are the basis for step, retry and token counts and for `run_integrity`.
- `<out>/checks/`: verification outputs referenced by the report, including the launcher's final re-execution.
- Host branch `dca/<run-id>`: the change set, created only if it is non-empty. It is never checked out or merged by the launcher.
