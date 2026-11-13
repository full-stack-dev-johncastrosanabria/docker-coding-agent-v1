# Architecture

DCA runs one bounded coding task in a fresh Docker Sandbox, verifies the final state, and returns
a report and, for a nonempty change, a Git branch. The host owns the lifecycle. See the
[implementation plan](../specs/001-bounded-coding-agent/plan.md),
[CLI contract](../specs/001-bounded-coding-agent/contracts/launcher-cli.md), and
[policy contract](../specs/001-bounded-coding-agent/contracts/policy-gate.md).

## Construction and runtime

`docker-agent.bootstrap.yaml`, `.agents/skills/` and `.specify/` support development of DCA.
They are not agent runtime assets. `scripts/verify.sh --static` checks that construction skills
and bootstrap references stay out of `runtime/`.

`runtime/` supplies the deployed agent configurations, instructions, four skills, policy and kit
installer. `runtime/sandbox/kit/stage.py` assembles the kit, hashes its trusted files into
`kit-manifest.json`, and stages the Python gate and fingerprint wrappers. Production uses
`/opt/dca` as the trusted kit root. This packaging establishes identity for cooperative checks;
an agent with VM sudo can still modify VM files. See the [threat model](threat-model.md).

Claude and Codex are alternative model backends. Root, Researcher and Reviewer are logical roles
within the selected backend. Root implements; Researcher investigates with read-only tools;
Reviewer evaluates a candidate without changing it. The four on-demand skills are
`repository-navigation`, `root-cause-debugging`, `verification` and `change-receipt`.

## Host and VM lifecycle

1. `bin/dca` enters `src/dca/cli.py`. The CLI resolves explicit options and checkout-local
   `.git/dca/config.toml` settings, canonicalizes the task, and creates a run request.
2. The launcher checks repository state, local-branch identity, eligibility evidence, version pins,
   network-policy fingerprint, SSH settings and backend authentication. An ineligible untrusted
   request produces a blocked report without creating a VM; an environmental precondition failure
   exits 3 without a completion report.
3. `source.py` bundles the selected branch's committed history. No host checkout is mounted.
   Ignored and untracked files, other branches, stash, Git hooks and host configuration are not
   delivered. Tracked secrets in the selected history remain repository content.
4. The host creates a mountless sandbox with shared skills off, applies its scoped network policy,
   copies the source bundle and trusted kit, then clones into `/workspace`. Codex's trusted token
   fallback copies only `chatgpt-auth.json` into an owner-only directory, removed with the VM.
5. The selected backend runs headlessly. Both backends use the shared policy gate. Codex also has
   native deny rules and an explicit `--safety strict`; `DOCKER_AGENT_KIT_DIR` fixes skill discovery
   to the staged kit. Agent reports and Context Records are claims, not host authority.
6. The host counts typed outer events and enforces its step, retry, token and wall-clock bounds.
   A timer enforces the deadline even during a silent tool call. When execution ends, the launcher
   reaps the local client and stops the VM workload before collecting evidence. `termination.json`
   records the enforced limits, stop timing and workload survivors.
7. The host directs final verification in the VM, stops leftover processes again, and retrieves a
   task-branch bundle. Quarantine validation checks the advertised identity and object integrity
   in an isolated repository before importing `dca/<run-id>`. The host checkout is not changed.
8. The sandbox is removed in cleanup. The finalizer combines agent claims with host-observed
   checks, stream integrity, limits, review fingerprints and the retrieved change set. It validates
   and writes `report.json` and `report.md`. An infrastructure abort writes no completion report.

## Components

| Path | Responsibility |
| --- | --- |
| `src/dca/cli.py`, `console.py`, `project_config.py` | Commands, progress, settings and precedence. |
| `src/dca/launcher.py`, `sbx.py` | Preconditions, sandbox lifecycle, execution, host limits, workload stops and retrieval. |
| `src/dca/eligibility.py`, `gates/` | Evidence freshness, backend eligibility and architecture/security experiments. |
| `src/dca/source.py`, `taskid.py` | Committed-source delivery, quarantine import and canonical task identity. |
| `src/dca/events.py` | Typed stream parsing, counters, ordering and run integrity. |
| `src/dca/policy_gate.py`, `policy_rules.py`, `shellparse.py` | Shared 31-class policy and conservative command/target classification. |
| `src/dca/grants.py` | Approval discovery, provenance and grants scoped to one new run. |
| `src/dca/fingerprint.py`, `fingerprint_hook.py` | Review fingerprints and mutation detection. |
| `src/dca/report.py`, `jsonschema.py` | Deterministic disposition, report validation and rendering, using the standard library. |
| `src/dca/kit_preflight.py` | In-VM trusted-kit checks before execution. |
| `runtime/agents/`, `instructions/`, `skills/` | Backend configurations and shared task behavior. |
| `runtime/policy/`, `claude/`, `sandbox/kit/`, `versions.yaml` | Policy data, managed Claude assets, packaging and pinned dependencies. |
| `benchmark/`, `src/dca/bench.py` | Deterministic fixtures, hidden oracles, thresholds, run evidence and comparison inputs. |
| `tests/unit/`, `tests/contract/`, `tests/integration/`, `tests/oracles/` | Deterministic verification, including isolated launcher execution and oracle goldens. |
| `.github/workflows/ci.yml` | Credential-free Layers A/B; live acceptance is local. |

## Evidence and enforcement

The host enforces source delivery, eligibility, network configuration, grants, timers, event
counting, final checks, quarantine import and disposal. In-VM gates, role restrictions and skill
identity checks are cooperative. Process-stop and fingerprint evidence is host-directed but still
depends on VM observations. These boundaries are detailed in the [threat model](threat-model.md).

The report and approval formats are defined by the
[completion report schema](../specs/001-bounded-coding-agent/contracts/completion-report.schema.json)
and [approval grant schema](../specs/001-bounded-coding-agent/contracts/approval-grant.schema.json).
Benchmark fixtures use the [fixture format](../benchmark/FORMAT.md) and
[fixture schema](../specs/001-bounded-coding-agent/contracts/fixture.schema.json).
