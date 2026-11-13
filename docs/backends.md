# Backends

Choose a backend before a run with `--backend claude|codex`, or with checkout-local configuration.
There is no backend switch during execution. Recorded eligibility is in
[gates/eligibility.json](../gates/eligibility.json); live readiness is checked by `bin/dca verify`.

| Property | Claude | Codex |
| --- | --- | --- |
| Runtime | Docker Agent `claude-code` harness | Native Docker Agent `chatgpt` provider |
| Agent config | [claude.yaml](../runtime/agents/claude.yaml) | [codex.yaml](../runtime/agents/codex.yaml) |
| Authentication | `sbx-host-oauth` | `token-file-trusted-only` |
| Trusted eligibility | Recorded PASS | Recorded PASS |
| Untrusted eligibility | Blocked: G1b and G9 | Blocked: G2, despite a G9 PASS |
| Logical roles | Root, Researcher, Reviewer | Root, Researcher, Reviewer |

## Claude

[G1a](../gates/G1a.json) proved an actual Claude Pro subscription through an interactive login,
then execution in a separate, completely fresh sandbox without another login, a TTY or a provider
key. The fresh sandbox reported null subscription metadata; this was not rewritten as Pro.
The login evidence establishes the plan, while fresh headless execution establishes reusability.

[Managed settings](../runtime/claude/managed-settings.json) and
[managed role definitions](../runtime/claude/agents/) provide the Claude-specific hook/tool
integration. [G1c](../gates/G1c.json) and [G1d](../gates/G1d.json) cover the tested policy and trusted
skill paths. The internally bypass-mode harness still requires DCA's shared gate and host controls.
This is a cooperative VM policy, as described in the [threat model](threat-model.md).

[G1b](../gates/G1b.json) found readable token material under privileged VM inspection. Claude
therefore remains trusted-only even though its normal execution and final production conformance
passed. [G9](../gates/G9.json) was ambiguous for Claude and fails closed.

## Codex

[G3](../gates/G3.json) recorded `gpt-5.5` as the model the native provider actually accepted after
`gpt-5.6` was rejected. The production config records the accepted model rather than relying on
what a model-list endpoint advertises.

[G2](../gates/G2.json) could not prove proxy-managed ChatGPT execution without a credential file.
The launcher therefore copies only the minimal `chatgpt-auth.json`, never the full host config
directory, into an owner-only VM directory for trusted runs. Disposal removes it. The file is
readable by a privileged workload, so this fallback cannot enable untrusted execution.

Every native invocation pins `--safety strict` and `DOCKER_AGENT_KIT_DIR` to the staged kit.
Native deny rules can reject an action before the hook; every other call must pass the shared
policy gate. Repository-local skills do not replace the trusted skill source. These properties
are covered by [runtime contracts](../tests/contract/test_runtime_configs.py),
[command-form integration tests](../tests/integration/test_launcher_execution.py), and
[production conformance](../gates/PRODUCTION-CONFORMANCE.json).

V1 does not use `harness: codex`. It uses the native provider/configuration that the gate evidence
tested. A future runtime upgrade must repeat the affected gates instead of assuming compatibility.

## Parity and limits

Both backends share the 31-class action policy, instructions, four trusted skills, host grant
validation, report schema, direct/planned limits, typed-event accounting, final host-directed
checks, bundle quarantine and benchmark fixtures. Backend-specific mechanisms supply those
shared behaviors; they do not create different acceptance standards.

[Backend parity tests](../tests/contract/test_backend_parity.py) check common assets and policy
paths. [G11](../gates/G11.json) and [production conformance](../gates/PRODUCTION-CONFORMANCE.json)
record live evidence. The [US1](../benchmark/results/2026-09-23-codex-US1.json) and
[US2](../benchmark/results/2026-09-27-codex-US2.json) campaigns are story evidence, not a completed
28-fixture release acceptance. Outstanding validation remains in
[tasks.md](../specs/001-bounded-coding-agent/tasks.md).
