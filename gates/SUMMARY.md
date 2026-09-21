# Gate review — architectural eligibility

Generated `2026-09-21T21:36:47Z` from the committed evidence in `gates/`, bound to
`runtime/versions.yaml` digest `sha256:4b4bf890b90c9cb93bb2957df9522ee4c039d80a3a7d252091f5ea6f31390088`.
Re-run with `python3 gates/review.py`; T062 and T073 re-run it as their evidence lands.

## Decision

**Trusted-only V1 remains viable and Phase 3/4 implementation may proceed.** No common
gate fails, both backends have a proven execution path, and both are architecturally
viable for the **trusted** profile. **Untrusted execution stays blocked on both backends**
and no stop rule fired.

`trusted_eligible` is `false` for both backends in `gates/eligibility.json`, and that is
expected rather than a finding: the accepted contract makes that flag mean *final runtime
readiness*, which additionally requires production conformance (**T062**) and G11 part B
(**T073**). Neither exists yet. Architectural eligibility for the trusted profile — the
question T024 actually decides — is established.

## 1–3. Availability and eligibility

| Backend | Available | Trusted-eligible (final) | Untrusted-eligible | Architecturally viable, trusted profile |
|---|---|---|---|---|
| claude | yes | yes | **no** | yes |
| codex | yes | not yet | **no** | yes |

## 4. Why Claude is trusted-only

**G1b is FAIL**: a privileged repository workload in the VM can read
Claude OAuth token material. That is the accepted security finding and the primary
blocker — untrusted eligibility requires G1b PASS.

**G9 is FAIL**, and the reason matters: `failure_reason=ambiguous_provider_response`,
`ambiguity_reason=rate_limited`, `capability_reproduced=not_observed`,
`capability_non_usability=not_proven`. Every workload
variant received a stable 429, including the one carrying no credential at all, so the
provider could not separate an authenticated caller from an unauthenticated one.
**No workload request reproduced authenticated capability** — this is inconclusive
evidence, not a breach — but non-usability was not proven either, and ambiguity fails
closed. G9 is therefore a secondary unresolved item, not the primary blocker.

## 5. Why Codex is trusted-only

**G2 is FAIL**: sbx's proxy-managed OAuth injects the OpenAI *platform*
credential family (`OPENAI_API_KEY`), while Docker Agent's native chatgpt provider requires
`CHATGPT_OAUTH_TOKEN`, and sbx exposes no `chatgpt` service. The two never meet, so the
failure is structural. Untrusted eligibility requires G2 PASS.

**G9 is PASS** for Codex (`capability_non_usability=proven`): no workload
variant, privileged or not, reproduced the control-plane capability. **That does not
override G2** — untrusted eligibility needs both.

## 6–7. Codex credential mechanism and model

- **Credential mechanism**: `token-file-trusted-only` — the minimal `chatgpt-auth.json`
  path G3 proved non-interactive in two fresh sandboxes. No provider API key, no
  `harness: codex`, and the full `~/.config/cagent` is never copied.
- **Model**: `gpt-5.5`, with `model_fallback_applied=true`.
  the preferred gpt-5.6 is LISTED by `docker agent models` and marked default, but the backend REJECTED it: The 'gpt-5.6' model is not supported when using Codex with a ChatGPT account.. T019's documented fallback selected gpt-5.5, the first candidate the backend accepted in preference order; it also rejected ['gpt-5.6']
- `in_vm_refresh_required=false`, so `auth.openai.com` is not
  promoted.

## 8. Remaining before an end-to-end `dca run`

- Phase 3/4 implementation (the policy core and the launcher), which this review unblocks.
- **T073 / G11 part B**: host step limits, retry cycles, wall-clock stop semantics and
  FR-023a. `gates/G11.json` is **PARTIAL** and stays that way until then;
  criteria 1–4 are proven for both backends, criteria 5–8 are not yet exercised.

## 9. Remaining before final production acceptance

- **T062 / production conformance** — `gates/PRODUCTION-CONFORMANCE.json` does not exist,
  so both backends record `production_conformance: NOT-RUN`.
- **T073 / G11 part B**, as above.
- Re-running `python3 gates/review.py` after each, which flips the typed flags without
  anyone editing this file.

### Sequencing hazard to check before T062 is accepted

`trusted_eligible` reads G11 from `gates/G11.json` `backends.<name>.status`, which part A
already set to `PASS`; the document-level `PARTIAL` is not what the flag consults. T073
depends on T062, so production conformance lands **first** — and at that moment this
review would compute `trusted_eligible: true` for both backends while G11 criteria 5–8
are still unproven. Execution is still blocked in practice, because `dca run` refuses
while G11 is not final PASS (launcher-cli precondition 7), so this is a reporting
inconsistency rather than an exploitable gap. It is recorded here rather than fixed:
changing it means amending accepted T020 evidence or the accepted T005 contract, which is
out of scope for this review. The narrow fix is for part A to record the per-backend G11
status as `PARTIAL` until part B completes it, which is what T073 already says it does.

## 10. Accepted architectural risks and fallbacks

- **claude**: no fallback applied
- **codex**: G3: model gpt-5.5; G2: credential mechanism token-file-trusted-only
- **Claude token readability (G1b)** is accepted: Claude stays a usable V1 backend but is
  trusted-only and never becomes untrusted-eligible without an architecture change.
- **Codex proxy-managed OAuth (G2)** is accepted as structurally unavailable; the trusted
  token-file path carries V1 instead.
- **Claude G9 ambiguity** is accepted as unresolved, not as a proven property in either
  direction.
- **Untrusted autonomous capability is not claimed for either backend.** S5a remains
  mandatory and split-plane agent/workload separation remains the recorded direction.

## Gate status

| Gate | Status | Scope |
|---|---|---|
| G0 | PASS | common |
| G4 | PASS | common |
| G5 | PASS | common |
| G6 | PASS | common |
| G7 | PASS | common |
| G8 | PASS | common |
| G10 | PASS | common |
| G1a | PASS | claude |
| G1b | FAIL | claude |
| G1c | PASS | claude |
| G1d | PASS | claude |
| G9 | FAIL | claude |
| G11 | PASS | claude |
| PRODUCTION-CONFORMANCE | PASS | claude |
| G3 | PASS | codex |
| G2 | FAIL | codex |
| G9 | PASS | codex |
| G11 | PASS — part A only; `gates/G11.json` is PARTIAL until T073 | codex |
| PRODUCTION-CONFORMANCE | NOT-RUN | codex |

G4 network policy fingerprint: `sha256:e282594fb4b738d4344dbf28c621d1b5f20f3e5104efbca7dd6861d801b96697`
