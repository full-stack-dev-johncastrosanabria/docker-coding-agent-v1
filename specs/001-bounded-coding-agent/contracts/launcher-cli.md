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
dca run [TASK]                              # the task text, quoted, or @file; exactly one of TASK and --task
        [--repo <path>]                     # default: the repository containing the current directory
        [--task <text|@file>]
        [--ref <branch>]                    # default: HEAD (must be attached to a local branch); local branch only
        [--backend claude|codex]            # default: local config, then claude
        [--trust trusted|untrusted]         # default: local config, then untrusted
        [--criteria @file]                  # acceptance criteria, one per line
        [--verify "<cmd>"]...               # declared required verification commands; replace the local config's
        [--approve <request-id>]...         # grant for THIS run, bound to a prior report's request
        [--approval-report <path>]          # originating report when it isn't at the default location
        [--ignore-uncommitted]              # explicit override: proceed from --ref despite a dirty checkout
        [--out <dir>]                       # default: <repo>/../.dca-runs/<run-id>/ (outside the repo)
        [--allow-drift]                     # permit version drift (recorded; not acceptance-eligible)
```

**Resolving the request (006).** Without `--repo`, the repository is the one containing the
current directory, which must be part of that repository's committed tree; anything else is a usage
error, never a guess at a parent repository. Supplying both `TASK` and `--task`, or neither, is a
usage error. Backend, trust and verification commands resolve independently: an explicit option,
then the checkout's local config (see `dca init`), then the default. Trust is therefore `trusted`
only when the developer chose it, on the command line or in the local config. Both forms build the
same request and run the same launcher.

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
4. **sbx availability and version**: `sbx` is missing or below 0.43.0 (needed for mountless `sbx create` and `--skills=off`), or its version or any other pinned version differs from `runtime/versions.yaml` without `--allow-drift`. The selected backend's `sandbox_bases` pin (G6/G1a evidence) must be present; a missing pin refuses the run even with `--allow-drift`.
5. **SSH agent**: sbx `ssh.agentForwardingEnabled` is true, or a fixed `ssh.agentSocketPath` is configured. The second is refused as a **deliberately strict V1 baseline** — a configured host agent socket is a forwarding intent V1 does not accept unverified — not as a claim that it forwards while the flag is false. A setting that can't be read is refused too, never read as "no agent". The launcher runs every `sbx` command with `SSH_AUTH_SOCK` removed. It never changes global sbx settings; it prints the one-time developer command instead.
6. **Backend authentication unavailable**: the login isn't present (safe status fields only). For Claude, the launcher offers `--backend codex` and doesn't switch automatically.
7. **Required gate evidence missing, invalid or stale**. The launcher reads only the host file `gates/eligibility.json` (computed by `gates/review.py` only from `gates/*.json` evidence whose `provenance` is still current for `runtime/versions.yaml`; never taken from the VM or the repository under test) and refuses when:
   - the file is missing, fails its schema (checked by a stdlib structural validator; the launcher never imports `jsonschema`), or was computed for pinned versions other than the installed ones;
   - the file is **stale**: its `runtime_versions_digest` (the canonical digest of the whole `runtime/versions.yaml`; tasks.md, Global constraints) or any explicit `pinned_versions` field differs from the current `runtime/versions.yaml`. Any changed pin, including the docker-agent artifact SHA-256 or a sandbox base, therefore requires a new gate review;
   - a backend-independent common gate (G0, G4, G5, G6, G7, G8, G10) isn't PASS. In particular, **G4 (effective network policy) not passed → no run of any profile proceeds**;
   - the selected backend isn't `available` or `trusted_eligible`: its availability gate (G1a for Claude, G3 for Codex), its trusted-profile gates (G1c and G1d for Claude), its **final G11 PASS** (a `PARTIAL` part-A-only status doesn't count), or its **production conformance PASS** is missing.

   Missing *untrusted-eligibility* gates (G1b/G2, G9) aren't a precondition failure; they lead to a Phase 2 `blocked` report (the S5a behavior).
8. **Stale, invalid or undiscoverable approval**: an `--approve <request-id>` whose originating report can't be located, or which fails provenance verification (see *Approval provenance*).
9. **Global network-policy drift**: the launcher reads the current global network-policy state (global preset, global rules, governance status; sandbox-scoped rules and run-scoped grants excluded), computes its fingerprint, and compares it with the fingerprint that G4 and production conformance recorded in `gates/eligibility.json`. Any difference refuses the run until G4 and conformance are re-run. The launcher never repairs or mutates global settings. If G4 made a specific global preset a V1 prerequisite, the diagnostic names it.

**Phase 2: policy disposition before provisioning.** The request is valid, but its correct
policy outcome is **BLOCKED**. The launcher **finalizes a `blocked` completion report and
exits 11**, with no sandbox, no model or agent execution, and no network activity for the run
(`run_integrity: {stream: none, agent_exit: not-started, sandbox_created: false}`). Cases:

- **Untrusted execution with no eligible backend**: `--trust untrusted` and the selected backend hasn't passed its secret-unreadability gate (G1b for Claude, G2 for Codex) **and** G9. `primary_reason` names the missing gates.
- **Required safe prerequisite unavailable**: detectable before provisioning, for example a task that requires Git LFS or submodules, which V1 doesn't deliver.

**Phase 3: execution.**

1. `git bundle create <tmp>/src.bundle <source.ref>` from the **named ref**, then `git bundle verify`, then confirm that the bundle's head for `source.ref` equals `source.commit`. **Failure here is an infrastructure abort**: exit 4, no report, and no sandbox is created.
2. `sbx create` of a **mountless** sandbox with `--skills=off` and the V1 kit, from the selected backend's **sandbox base**:
   - **Claude**: the exact Docker Sandboxes Claude base (the sbx `claude` agent variant) that G6 and G1a proved;
   - **Codex**: the exact docker-agent sandbox template that G6 proved.

   The exact identifiers and versions come from `runtime/versions.yaml` `sandbox_bases`, which G6 (and G1a for Claude) record. The launcher never resolves, guesses or substitutes a base at runtime; a missing or drifted pin is a precondition failure (precondition 4).
3. Apply the strict network policy for the profile, plus the host-authoritative network grants.
4. `sbx cp` the bundle and run config into the VM; the VM clones the bundle to the workspace.
   - **Codex trusted token-file fallback only**: when the selected backend is Codex, the run is **trusted**, and `gates/eligibility.json` records `credential_mechanism: token-file-trusted-only`, the launcher also copies a minimal config dir containing **only** `chatgpt-auth.json` (never the full Docker Agent config dir) into the VM, owner-only. It never logs the file's contents or any hash of it. An untrusted request can never select or receive it, because Phase 2 already blocks untrusted Codex under this mechanism. With `proxy-managed`, nothing is copied. The material is removed with the sandbox, and sandbox disposal stays mandatory.
5. Kit/gate preflight inside the VM (the gate executes and fails closed; versions match; the canonical kit manifest `<KIT_DIR>/kit-manifest.json` is valid, every runtime skill matches its hash, and nothing else exists under `<KIT_DIR>/skills/` ([policy-gate](policy-gate.md) *Kit manifest*); for Codex, the in-VM Docker Agent user config has no `permissions`, `safety`, `yolo` or alias options). The VM creates the task branch `dca/<run-id>` at `source.commit`. **Any failure in steps 2–5 is an infrastructure abort**: exit 4, no report, best-effort `sbx rm`.
6. `sbx exec docker agent run --exec --json …`. For **every native Codex execution** the command includes an explicit **`--safety strict`**, which outranks user-level Docker Agent settings, and an explicit environment entry **`DOCKER_AGENT_KIT_DIR=<KIT_DIR>`**. That value is built from the trusted staged-kit root (`/opt/dca` in production), never from repository content or the inherited environment, so Docker Agent discovers skills only from `<KIT_DIR>/skills` and a same-named repository skill can't replace a runtime skill. `dca` has no option to change either. The Claude command is unchanged; Claude's skill source is covered by G1d. The launcher parses **typed outer Docker Agent events**, counts steps and retries, enforces wall-clock with a host timer, and fails closed on a malformed or truncated stream or an abnormal exit (evidence: G11).
7. Final re-execution of the required deterministic checks on the final state.
8. Retrieval. The task-branch bundle is created in the VM from the named ref `dca/<run-id>` and copied out via `sbx cp` into a host quarantine directory outside `.git`. It is untrusted until the **complete quarantine validation** passes, in this order: **`git bundle verify`** → **exact advertised identity** (exactly one head; SHA equals the expected candidate commit; ref is exactly `refs/heads/dca/<run-id>`; equality, never a prefix or substring match) → **object-level integrity validation** (an isolated quarantine repository, or an equivalently strong `index-pack`/unpack check; R11 records that `git bundle verify` alone accepted a truncated bundle on the observed Git version) → **only then** the host import, fetching exactly `dca/<run-id>`. **The launcher does not touch the developer repository — no fetch, no ref write, no object write — before that whole validation has succeeded.** **Any failure in export, copy, any validation stage, or import is an infrastructure finalization failure**:
   - exit 4;
   - never `succeeded`;
   - no completion report claiming a task disposition;
   - only safe diagnostic and event artifacts are kept (`events.jsonl`, gate log);
   - best-effort `sbx rm`;
   - **no partial `dca/<run-id>` branch is ever fetched or created**.
9. Report finalization, then `sbx rm`.

**Gate harness (internal, G11 part B only).** Normal `dca run` keeps refusing execution while a
backend's G11 status isn't final PASS (precondition 7). The G11 part-B procedure,
`gates/G11/run_part_b.py`, therefore calls the launcher's already-implemented Phase 3 execution
primitives directly. Its own preconditions are G11 part A PASS for that backend, production
conformance PASS, and every other common gate and backend gate needed to exercise the backend.
Its Codex executions use the same command form as step 6, including `--safety strict` and
`DOCKER_AGENT_KIT_DIR=<KIT_DIR>`. It is available only to the gate procedure. There is **no** public `--skip-gates`,
`--ignore-g11`, `--unsafe` or similar flag, and nothing in `dca run` accepts a partial G11.

Task-run outcomes during execution map to exit codes 0, 10 or 11, provided retrieval (step 8) succeeds. They include approval required with no grant (the gate's `DCA_APPROVAL_REQUIRED`), a required prerequisite or check that can't run, host-enforced limits, a **native Docker Agent ceiling termination** (`limit_reached: native_ceiling`, recorded with the event's detail; `succeeded` only under FR-023a), and an **abnormal agent exit or malformed/truncated event stream**. These last two stay task-run `blocked` outcomes (exit 11) because G11 defines their evidence path; they are not infrastructure aborts.

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

### `dca init`

```text
dca init [--repo <path>]                    # default: the repository containing the current directory
         [--backend claude|codex]           # default: claude
         [--trust trusted|untrusted]        # default: untrusted; `trusted` only by explicit choice
         [--verify "<cmd>"]...
         [--overwrite]                      # replace an existing config with different settings
```

Writes the checkout's **local config**, `<git-dir>/dca/config.toml` (`.git/dca/config.toml`, or a
linked worktree's own git directory), mode `0600`. Git never checks that directory out, so a cloned
repository can't provide the file, and neither `git status` nor the source bundle includes it.
Re-running with the same settings changes nothing; different settings are refused (exit 2) without
`--overwrite`.

The file is TOML with exactly these keys: `version = 1`, `repository` (the checkout root it was
written for), `backend`, `trust`, and `[verification] commands`. When `dca run` reads it, the
following fail closed with exit 2 before any launcher phase:

- an unknown key;
- an invalid value;
- a `repository` other than the current checkout;
- a symlink;
- a file writable by group or others.

It can't express policy, network, safety, limits, credentials or hooks.

### `dca verify`

```text
dca verify [--repo <path>]                  # default: the repository containing the current directory, if any
```

Runs the `scripts/verify.sh` checks and presents their named results grouped for the developer
(Docker Sandboxes, network policy, version pins, runtime assets, gate evidence, environment, each
backend), each failure with its reason and a next step. It also reports:

- the selected checkout's state and its local config;
- any `dca-*` sandbox left behind;
- whether untrusted runs are blocked.

It exits non-zero on any `verify.sh` failure or an invalid local config, and never prints tokens.

### `dca bench`

```text
dca bench [--backend claude|codex|both] [--trust trusted|untrusted] [--fixtures <glob>[,<glob>...]]
          [--repeat N]          # default 1; acceptance uses 3
          [--acceptance]        # requires committed thresholds, clean tree, exact pinned versions, passed gates
```

V1 ships a 10-fixture reliability suite (K1-K8, M1-M2; `benchmark/FORMAT.md`). Every fixture run is
a real `dca run` subprocess, results go to `benchmark/results/<bench-id>/benchmark.{json,md}`, and
the exit status is 0 only when every run passed. `--acceptance` is refused (exit 3) until the full
28-fixture suite and `benchmark/thresholds.yaml` exist.

Fixtures are selected per backend by `gate_condition`. Exactly one of S5a (untrusted
fail-closed, expected `blocked`) and S5b (untrusted egress/G9) applies to each backend.

**Trust resolution and counting** (deterministic; research R23/R24):
- `--trust <P>` selects the benchmark profile `P` (default `trusted`). For each fixture:
  `trust_level: both` runs under `P`; `untrusted` always runs untrusted; `trusted` runs trusted
  under `P = trusted` and is `not-applicable` under `P = untrusted`. `--trust` therefore both
  supplies the profile for `both` fixtures and filters out `trusted`-only fixtures.
- In V1 every fixture except S5a/S5b is `both`, and S5a/S5b are `untrusted`
  ([fixture schema](fixture.schema.json)). So:
  - **trusted acceptance** runs 27 `both` fixtures trusted plus the applicable S5 variant
    untrusted: **28** applicable (small 8, medium 6, failure-recovery 6, safety 8);
  - **untrusted capability acceptance** runs 27 `both` fixtures plus S5b, all untrusted: **28**
    applicable with the same category counts. It uses the same per-run thresholds (small ≥ 7/8,
    medium ≥ 4/6, failure-recovery 6/6, safety 8/8, aggregate ≥ 25/28, zero SC-005–SC-009
    violations), with at least 3 clean runs each meeting them independently.
- `--trust untrusted` for a backend that isn't `untrusted_eligible` is refused with exit 3 and
  runs nothing. Fail-closed untrusted handling is proven by S5a in every trusted run instead, and
  no autonomous untrusted coding is claimed for that backend.
- A `not-applicable` fixture is listed but counts in neither numerator nor denominator. An
  applicable fixture that yields no result, or a report that fails the report schema, **counts
  as a failure**, never as a pass. The runner refuses to score a run whose applicable set isn't
  exactly 28 with the category counts above.
- `--acceptance` additionally requires committed thresholds, a clean tree, exact pinned
  versions, a valid `gates/eligibility.json`, and every gate and production conformance PASS for
  the selected backend (untrusted-eligibility gates too when `P = untrusted`).

## Exit codes

| Code | Meaning | Report written? |
|---|---|---|
| 0 | Run finalized, `final_outcome = succeeded` | yes |
| 10 | Run finalized, `final_outcome = failed` | yes |
| 11 | Valid request whose correct policy outcome is `blocked`: untrusted execution with no backend satisfying the required gates; approval required with no grant; required safe prerequisite unavailable; a required check that can't run; a host-enforced limit or a native Docker Agent ceiling termination without prior success evidence; malformed/abnormal run. The report lists the human action | yes |
| 3 | Precondition failure before any task disposition: sbx unavailable, incompatible or drifted version, backend authentication unavailable, dirty checkout without override, provider API-key contamination, SSH forwarding active, required gate evidence missing, invalid or stale (`gates/eligibility.json`; e.g. G4 effective network policy not passed; the backend unavailable, its final G11 or its production conformance not PASS), global network-policy fingerprint drift, stale or invalid approval; for `dca bench`, `--trust untrusted` on a backend that isn't untrusted-eligible | no (diagnostic only) |
| 2 | Usage error: invalid arguments, no task or two tasks, no repository to detect, or an invalid local config | no |
| 4 | Infrastructure abort: no task disposition and never `succeeded`. Three cases: (a) the **source bundle** can't be created or validated (before any sandbox); (b) **provisioning** fails before the agent starts (`sbx create`, network policy, `sbx cp`, in-VM clone, kit/gate preflight); (c) **retrieval** fails after the agent ran (task-branch bundle export, copy, verify or import), so no trustworthy change set exists. The launcher does best-effort `sbx rm`, keeps only safe diagnostic and event artifacts, and never creates a partial `dca/<run-id>` branch. Never reported as a task `blocked` outcome | no (diagnostic only) |

## Outputs

- `<out>/report.json`: matches [completion-report.schema.json](completion-report.schema.json).
- `<out>/report.md`: a short human summary rendered from the JSON.
- `<out>/grants.json`: the host-authoritative grants for this run, if any.
- `<out>/gate.log.jsonl`: the in-VM policy-gate decision log, copied out. It is VM-originated, cooperative evidence and contains no secret values.
- `<out>/events.jsonl`: the host-captured typed outer Docker Agent events, which are the basis for step, retry and token counts and for `run_integrity`.
- `<out>/context.json` and, for planned tasks, `<out>/plan.md`: the agent's Context Record and Plan, copied from the run scratch dir. They are VM-originated evidence ([data-model](../data-model.md#context-record)), never an authority for grants, host policy or limits.
- `<out>/checks/`: verification outputs referenced by the report, including the launcher's final re-execution.
- Host branch `dca/<run-id>`: the change set, created only if it is non-empty. It is never checked out or merged by the launcher.
