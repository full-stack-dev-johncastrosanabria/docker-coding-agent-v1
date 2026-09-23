# Benchmark fixture format

Each fixture is a directory `benchmark/fixtures/<id>/`, and its `fixture.yaml` must validate against
[`fixture.schema.json`](../specs/001-bounded-coding-agent/contracts/fixture.schema.json). The file is
JSON-compatible YAML, read with the stdlib `json` module only. IDs follow the schema. The committed
reliability suite is `R1`–`R10`: `R1`–`R8` are small/direct tasks and `R9`, `R10` are medium/planned
tasks. `K*`, `M*`, `F*` and `S*` are reserved for the acceptance fixtures that T079–T089, T092 and
T094 create. Before 007 the reliability suite was `K1`–`K8`, `M1`, `M2`; the mapping is in the
[baseline](baselines/reliability-2026-09-22.md#fixture-ids).

| Path | Purpose |
|---|---|
| `fixture.yaml` | Task text, acceptance criteria, verification commands, expected disposition and classification, and `allowed_change_scope` globs. |
| `seed/` | The starting repository as plain files. It includes a `.gitignore` for bytecode, so a verification run is not reported as a change. |
| `oracle.sh` | Decides whether a delivered candidate is correct, and exits 0 on pass. |
| `hidden/` | Tests the oracle adds and the agent never sees. |
| `mutants/` | R3 only: wrong implementations that the agent's new tests must detect. |
| `golden/good.patch`, `golden/bad/*.patch` | A reference solution and plausible wrong answers, used to validate the oracle. |

## Seed determinism

`dca.bench.build_seed` copies `seed/` without file modes and skips `.DS_Store`. It commits on `main`
with fixed author, committer and `2000-01-01T00:00:00Z` dates. Identical seed bytes therefore always
give the **same commit SHA**. That SHA is the fixture's identity and is recorded in every benchmark
result. No bundle is committed, because git bundle bytes are not stable across git versions and a
commit SHA is.

## Oracle interface

`oracle.sh` receives `CANDIDATE_DIR` (read-only) and `FIXTURE_DIR`. It sources
`benchmark/tools/oracle_lib.sh`, which copies the candidate to a scratch directory and then:

- runs the candidate's own tests as delivered;
- restores the seed's **original** tests over the candidate's copies, so a weakened test cannot
  pass, and adds `hidden/`;
- can require that files the task says to leave alone are unchanged (`unchanged <path>`).

The runner applies the generic checks: report schema, final outcome, allowed scope, and sandbox
cleanup.

Oracles use only POSIX `sh`, `python3` and `node`. For agent-produced candidates, `dca bench` runs
them in the pinned `sandbox_bases.claude` image, by digest, with `--network none` and the
candidate mounted read-only. Agent output is never executed on the host. Golden patches are
repository-owned and are validated on the host:

```sh
python3 tests/oracles/run_oracles.py        # the seed fails, good.patch passes, every bad patch fails
```

## Rules for fixtures

- They are tiny, self-contained and offline. They need no dependency installation, only the Python
  and Node already in the sandbox base images.
- A task prompt stays stable once a campaign has used it. A fixture changes only for a fixture
  defect, never to make a model pass.
- Every fixture has at least two bad patches, each a realistic wrong answer the oracle must reject.
