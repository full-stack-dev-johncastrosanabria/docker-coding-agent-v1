# Threat model

V1 supports trusted repositories on the backends recorded in [eligibility.json](../gates/eligibility.json).
Autonomous untrusted coding is blocked. Historical gate results apply to the exact
[runtime pins](../runtime/versions.yaml) and recorded network policy; they do not establish safety
for a later installation with different binaries or settings.

## Trust boundaries

The host launcher and its local evidence, settings and approval reports are the control plane.
Repository files, task outputs, event text, agent reports and result bundles cross into it as data.
Repository instructions and `env` files cannot authorize tools, expand network access, supply
host-authoritative grants or override backend eligibility.

The VM contains the runtime agent and repository workload. The agent has sudo there, so the
policy gate, managed assets, role restrictions, skill hashes, process table and fingerprints are
cooperative controls or observations. Root ownership inside this VM is not a separate security
boundary. Host-side sandbox isolation, network policy and quarantined retrieval provide the
boundaries against the developer's machine. A Git branch alone provides no security isolation.

No checkout or credential-store directory is mounted. Source delivery includes the selected
branch's committed history, so it does not remove a secret already committed there. Ignored
host files are excluded by delivery; `.agentsignore` is context hygiene, not an access control.

## Credentials and model capability

Secret unreadability and capability non-usability are separate requirements. A workload might be
able to invoke a proxy-authenticated model endpoint without reading a credential. Conversely,
blocking that endpoint does not protect a token file the workload can read.

Claude's [G1b](../gates/G1b.json) found token material readable by a privileged VM workload.
[G9](../gates/G9.json) did not prove Claude capability non-usability: its response was ambiguous
because of rate limiting. This is a failed gate, not proof that the capability was reproduced.

Codex's [G2](../gates/G2.json) could not execute the native ChatGPT provider through proxy-managed
OAuth. Trusted runs therefore use the proven `chatgpt-auth.json` fallback, which a privileged
repository workload can read. Codex passed its G9 capability probe, but the G2 credential
requirement still fails. Neither backend is untrusted-eligible. Split-plane agent/workload
separation remains the recorded direction for closing this gap.

Credential values, partial values and credential hashes must not enter prompts, gate evidence or
reports. The launcher refuses provider-key environment variables, removes SSH agent forwarding
from the execution environment, redacts credential-shaped stderr, and disposes of the VM. Output
redaction is defense in depth; it does not make a trusted run safe for hostile repository code.

## Recorded gate outcomes

| Evidence | Result and implication |
| --- | --- |
| [G0](../gates/G0.json) | PASS: the pinned sandbox baseline and global settings were established. |
| [Inventory](../gates/INVENTORY.json), [G4](../gates/G4.json) | PASS: scoped default-deny networking was checked against effective rules, connection probes and logs. A host appearing in provider documentation alone does not authorize it. |
| [G5](../gates/G5.json) | PASS: source/result bundle round-trip and whole-bundle quarantine validation; header verification alone is insufficient. |
| [G6](../gates/G6.json) | PASS: trusted-kit installation from the pinned base and runtime artifact. |
| [G7](../gates/G7.json) | PASS: no usable SSH agent in the VM. |
| [G8](../gates/G8.json) | PASS: shared host skills are absent. |
| [G10](../gates/G10.json) | PASS: committed-source delivery excludes ignored host files and other branches; dirty checkout refusal was exercised. |
| [G1a](../gates/G1a.json) | PASS: Claude Pro login and a separate fresh headless sandbox execution. The inherited credential's subscription metadata was null, recorded as observed. |
| [G1b](../gates/G1b.json) | FAIL: Claude token material readable; trusted-only. |
| [G1c](../gates/G1c.json), [G1d](../gates/G1d.json) | PASS: Claude tool-policy and trusted-skill behavior under the tested configuration. |
| [G3](../gates/G3.json) | PASS: native Codex execution and strict approval lane; model fallback is `gpt-5.5`. |
| [G2](../gates/G2.json) | FAIL for proxy execution; token-file fallback accepted only for trusted Codex. |
| [G9](../gates/G9.json) | Claude FAIL (ambiguous); Codex PASS. Neither result overrides secret-isolation requirements. |
| [G11](../gates/G11.json) | Final PASS for both backends, including the host limit-enforcement path. Typed outer events, rather than text imitating events, drive counters. |
| [Production conformance](../gates/PRODUCTION-CONFORMANCE.json) | PASS for the recorded final assets on both trusted backends. |

The launcher recomputes the global network-policy fingerprint and checks evidence provenance.
It refuses drift; it does not repair global policy. Intentional runtime or policy upgrades require
new affected gate and production-conformance evidence before acceptance.

## Residual risks and approvals

A trusted repository can execute code in the VM, read credentials available there, and use the
profile's allowed network destinations. A default-deny allowlist limits destinations, not the
meaning of traffic to an allowed service. The cooperative gate cannot provide containment against
a privileged adversary. The same limitation applies to a deliberately forged VM process table or
review evidence. Final checks and oracle tests reduce false success, but are not proofs of arbitrary
program correctness.

ASK actions need a host-validated grant tied to the original report digest, task fingerprint,
source commit, backend, trust and normalized action target. Grants apply to one new run. DENY
actions cannot be granted. See the [approval contract](../specs/001-bounded-coding-agent/contracts/policy-gate.md).

The Docker Agent binary is pinned by SHA-256 to a publisher release-asset digest, as
`docker_agent_artifact.verification` records in [versions.yaml](../runtime/versions.yaml). This
identifies the downloaded release bytes; it does not claim an independent source build or signed
attestation. Sandbox images are digest-pinned. Any pin change requires the corresponding proofs
to be renewed.
