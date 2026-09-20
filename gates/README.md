# Verification gates

Gates G0–G11 prove or disprove every sandbox-dependent assumption **before** any work builds on it (tasks.md Phase 2; research.md "Verification gates"). **A failed gate is never "fixed" by editing the spec.**

Nothing in this directory records a gate result until the gate has actually been run. Synthetic documents used by tests live under `tests/fixtures/`, never here.

## Layout

- `gates/<ID>/`: the gate procedure (a minimal `run.sh` plus helpers) and its local `work/` directory (git-ignored).
- `gates/<ID>.json`: machine-readable evidence, validated against [`evidence.schema.json`](evidence.schema.json). Status is exactly one of `PASS`, `FAIL`, `PARTIAL` (G11 only, until part B), `NOT-RUN` (with `not_run_reason`) or `NOT-APPLICABLE`.
- `gates/eligibility.json`: computed by the gate review (T024, `gates/review.py`) and validated against [`eligibility.schema.json`](eligibility.schema.json) and the rule checker [`eligibility_rules.py`](eligibility_rules.py). It is the only gate evidence the launcher reads.

## Gate order

| Order | Task | Gate | Purpose |
|---|---|---|---|
| 1 | T008 | G0 | Environment, exact pin, one-time global SSH configuration, bootstrap network preset when uninitialized |
| 2 | T009 | G6 | Kit mechanics, Python version and artifact pin |
| 3 | T010 | G7 | SSH-agent isolation |
| 4 | T011 | G8 | Shared-skills isolation |
| 5 | T012 | G10 | Sanitized source delivery |
| 6 | T013 | G5 | Bundle round-trip, retrieval and disposal |
| 7 | T014 | INVENTORY | Control-plane host inventory (gate prep; not a G-number) |
| 8 | T015 | G4 | Effective strict network policy (runs serialized, no other sandbox active) |
| 9 | T016 | G1a | Claude Pro subscription execution, fresh-sandbox authentication |
| 10 | T017 | G1c | Managed settings and environment hardening |
| 11 | T018 | G1d | Managed Claude subagents, skills and memory |
| 12 | T019 | G3 | ChatGPT gpt-5.6 availability, trusted fallback provisioning, approval pipeline |
| 13 | T020 | G11 part A | Event-stream integrity (stays PARTIAL until T073) |
| 14 | T021 | G1b | Claude secret unreadability |
| 15 | T022 | G2 | ChatGPT OAuth isolation and mechanism selection |
| 16 | T023 | G9 | Control-plane capability non-usability (attacker-equivalent oracle with positive control) |
| 17 | T024 | REVIEW | Gate review: computes gates/eligibility.json (not a G-number) |
| 18 | T062 | PRODUCTION-CONFORMANCE | Production-asset conformance (not a G-number) |
| 19 | T073 | G11 part B | Host-side limit enforcement through the internal gate harness |

Every gate runs serially in this order, except that G8 (T011), G1d (T018) and G3 (T019) are parallel-safe with each other once their prerequisites are complete. G4 (T015) runs with no other sandbox active.

## Stop rules (copied verbatim from tasks.md, never weakened)

- If G0, G4, G5, G6, G7, G8 or G10 FAILs and its fallback doesn't pass, **stop** all gated work. Only the host-independent Phases 3–4 may continue.
- G11 is required for every backend but judged **per backend**: a G11 failure makes only that backend unavailable. If no backend passes G11, stop all gated work.
- G1a/G1b/G1c/G1d failures affect only Claude.
- G2/G3 failures affect only Codex, using the recorded fallbacks.
- G9 failure means trusted-only V1 (accepted developer decision).

**Backend completion rule**: every backend-specific gate task **always** finishes with `PASS`, `FAIL` or `NOT-RUN` (the latter with `not_run_reason`, e.g. "backend unavailable: G1a FAIL"). Aggregating tasks depend on **all** backend gate tasks and evaluate their statuses.

## Fallbacks (copied verbatim from the research.md gate table, "If it fails" column)

| Gate | If it fails |
|---|---|
| G0 | Nothing sandboxed can proceed |
| G1a | Claude profile unavailable on the Pro plan; report blocking; Codex becomes the only candidate (never an API key) |
| G1b | Claude restricted to **trusted** runs |
| G1c | Claude profile not safe → blocking |
| G1d | Fall back to unique names + gate verification of the subagent prompt hash and of the skill source and hash |
| G2 | If it passes: preferred for **both** profiles (G9 still required for untrusted). If it fails (**developer decision, recorded**): the native ChatGPT provider stays, trusted runs use the documented `chatgpt-auth.json` copy fallback, and untrusted Codex requests remain `blocked`. **No `harness: codex` in V1**; it is a V1.1 candidate only with evidence |
| G3 | Pin the available GPT-5.x model and record it. If the approval pipeline doesn't behave as stated, Codex is unavailable (no weaker safety mode is substituted) |
| G4 | **No fallback.** No trusted or untrusted run proceeds. The launcher refuses execution (exit 3, gate not passed) until the network design is corrected. V1 never falls back to Docker's Balanced or default broad allowlist |
| G5 | Redesign delivery or retrieval |
| G6 | Custom template instead of kit |
| G7 | Run refused until forwarding is disabled |
| G8 | Run refused |
| G9 | **Untrusted runs stay blocked** on that backend (trusted-only V1; S5a stays mandatory); split-plane agent/workload separation recorded as the required direction |
| G10 | Run refused |
| G11 | Bounded execution is unproven on that backend, so it is not accepted for any run until fixed |

## No-secrets rule (copied verbatim from tasks.md, global constraints)

- **Gate evidence** never contains secret values, tokens, authorization headers, sensitive response bodies, or file contents that could hold credentials. It records only booleans, status codes, byte lengths, pattern IDs, paths and hashes.
- **Global sandbox settings** (`ssh.agentForwardingEnabled`, the global network preset) may change **only during an explicitly developer-authorized gate execution**, as recorded one-time actions. The gate procedure performs the commands, so nothing has to be typed by hand, but it is only ever started by the developer: **G0 is never launched autonomously**. **The launcher and normal agent runs never mutate global sandbox settings.** Every such change records its before and after state, and the gate stops before the change unless all of its prerequisites hold.

`evidence.schema.json` enforces this structurally: every object is closed (`additionalProperties: false`) and map keys that look like secrets (token, secret, password, credential, authorization, cookie, api key, bearer) are rejected.

## Global state G0 may change

G0 is the only gate that changes global sbx state, and only these two things:

1. `ssh.agentForwardingEnabled` → `false`, followed by `sbx daemon restart` (R14). PASS requires the re-read value to be `false` **and** `sbx diagnose --json` to report the daemon healthy after the restart: the stored value alone doesn't prove the change took effect. G7 is the later in-sandbox proof that no SSH agent is reachable.
2. The **bootstrap network preset**, only when `sbx policy ls --json` reports the global policy uninitialized in exactly the representation observed from the candidate sbx (exit code, empty stdout and the full stderr text, matched byte for byte rather than by substring, because this is what authorizes the mutation). sbx requires a preset before the first sandbox runs and, non-interactively, it must be set with `sbx policy init` ([Local policy](https://docs.docker.com/ai/sandboxes/security/policy/)); G6 creates sandboxes before G4 runs. G0 then initializes `deny-all` (Locked Down: no baseline allow rules, though a kit or an explicit rule can still add per-sandbox allowances) and records `previous_state`, `bootstrap_preset` and the reason. Never `allow-all`, never `balanced`. An existing preset or governance state is recorded and left unchanged, and governance is recorded as `unknown/not observable` unless the CLI reports it.

The two changes are strictly ordered and each is guarded: preconditions → SSH change → proof it took effect → policy bootstrap. A failure at any guard stops the run before the next global change, so a failed SSH step can never be followed by `sbx policy init`.

**G4 remains authoritative** for the effective policy, the must-deny proofs and the `network_policy_fingerprint`; a `deny-all` bootstrap is a starting point, never evidence that the network policy passed. The launcher never initializes or mutates global settings.

`sbx ls --json` is accepted only in the observed `{"sandboxes": [...]}` shape, and, since the observed list was empty, any listed sandbox blocks the change until a per-entry status field is empirically observed.

Until G0 passes, the installed sbx version is the **observed candidate**, not the V1 pin: `runtime/versions.yaml` records `sbx.exact` and `claude_code.exact` only on a PASS. G0 decides each criterion from the JSON surfaces plus the exit status of the deterministic commands; `sbx diagnose` is the exception, since it exits non-zero when any check fails, including ones outside G0's decision set, so only its named checks (`Daemon`, `Version match`, `Authentication` — names observed in the candidate version) decide.

## Evidence provenance

Every `gates/<ID>.json` records, in its typed and closed `provenance` field, the environment it actually proved. `runtime/versions.yaml` is filled in progressively (G0 writes the exact sbx and Claude Code versions; G6 writes the docker-agent artifact and both sandbox bases), so the binding depends on the gate:

| Evidence | `provenance` | Current while |
|---|---|---|
| G0 | `pins`: `docker_agent`, `docker_agent_config_version`, `sbx` (exact), `claude_code` (exact) | every recorded pin equals `runtime/versions.yaml` |
| G6 | `pins`: `docker_agent_artifact_sha256`, `sandbox_bases.{claude,codex}.{base,version}`; `runtime_versions_digest` of the post-G6 file | every recorded pin and the digest equal the current file's |
| Every other document (G1a–G5, G7–G11, INVENTORY, PRODUCTION-CONFORMANCE) | `runtime_versions_digest` of the file in force when the gate ran | the digest equals the current file's |

The digest is the canonical one (tasks.md, Global constraints). Gate procedures take their provenance from `evidence_provenance()` in [`eligibility_rules.py`](eligibility_rules.py). A G0 PASS records every G0 pin, and a G6 PASS records the artifact SHA-256 and both sandbox bases. G11 is one document: part B (T073) may complete it only while part A's digest is still current; otherwise part A is re-run too.

Evidence that is no longer current is **stale**. A stale gate is never counted as PASS and never turned into FAIL: it must be re-run.

## Pin binding

`eligibility.json` records the explicit pins (`pinned_versions`: docker-agent version, config version and artifact SHA-256, exact `sbx`, exact Claude Code, and each backend's sandbox base and version) and `runtime_versions_digest`, the canonical digest of the whole `runtime/versions.yaml` the review ran against (tasks.md, Global constraints). `python3 gates/eligibility_rules.py gates/eligibility.json runtime/versions.yaml` reports the document as **stale** when either differs from the current file, so any changed pin, including the docker-agent artifact or a sandbox base, requires a new gate review (T024). A trusted-eligible backend must have all of its pins non-null.

A new review can't re-bind old evidence to new pins: **eligibility may be recomputed only from evidence that is still valid for the current environment.** `gates/review.py` checks every evidence document first (`check_evidence`) and, if any is stale, writes no `eligibility.json`, lists the gates to re-run and exits non-zero. After writing, `python3 gates/eligibility_rules.py gates/eligibility.json runtime/versions.yaml gates` (`check_review`) must exit 0: the document is consistent and bound, all evidence is current, and every recorded status equals its evidence (missing evidence counts as NOT-RUN).
