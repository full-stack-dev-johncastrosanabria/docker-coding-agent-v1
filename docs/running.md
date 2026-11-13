# Running DCA

Use a checkout of this repository with Python 3.11+, Git, Docker Desktop and the exact versions in
[runtime/versions.yaml](../runtime/versions.yaml). The recorded release environment is macOS/arm64.
See [README setup](../README.md#installation-and-setup) for the full initial installation.

## Install and authenticate

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -e .
.venv/bin/dca --help
export PATH="$PWD/.venv/bin:$PATH"
```

An editable install keeps runtime assets and gate evidence next to the code. `bin/dca` also works
directly from the checkout. A copied install refuses to run without those assets.

Download the pinned Linux/arm64 Docker Agent binary from the URL in `versions.yaml` to
`gates/G6/work/docker-agent-linux-arm64`, or set `DCA_DOCKER_AGENT_ARTIFACT` to its location.
Compare `shasum -a 256 <path>` with `docker_agent_artifact.sha256`; DCA checks it again when staging.

Sign in to Docker Sandboxes with `sbx login` if `sbx ls --json` reports no Docker session.
Claude uses the host subscription login; inspect only `claude auth status --text`.
For Codex use `docker agent setup` and select ChatGPT, on the pinned runtime. Do not copy login
output or credential-file contents into task prompts or reports.

Provider keys must be absent, even when their value is empty. This prints names only:

```sh
python3 - <<'PY'
import os
for name in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY"):
    if name in os.environ:
        print(name)
PY
```

SSH forwarding must be off, with no configured agent socket. If needed, set
`sbx settings set ssh.agentForwardingEnabled false` and restart with `sbx daemon restart`.
The accepted global policy is the `default-deny-all` baseline recorded by
[G0](../gates/G0.json) and [G4](../gates/G4.json). Initialize a policy with
`sbx policy init deny-all` only when the installation reports it uninitialized. Do not replace an
existing global policy to clear a failure. DCA checks its fingerprint and never repairs it.

## Configure a target repository

From a clean local Git checkout on an attached branch:

```sh
dca init --backend claude --trust trusted --verify "python3 -m unittest -q"
dca verify
dca run "Fix the median calculation for even-length inputs."
```

`dca init` writes owner-only `.git/dca/config.toml` for that checkout. Explicit CLI options win over
this config, which wins over defaults; `--verify` replaces the configured commands. Trust defaults
to `untrusted`, which current eligibility blocks. Select `trusted` only for a repository you trust.
Unknown keys, unsafe file permissions, a symlink or a different checkout identity cause exit 2.
Changing an existing configuration requires `dca init --overwrite`.

The task can be positional, `--task "..."`, or `@path/to/task.txt`. The explicit form also works
outside the target checkout:

```sh
bin/dca run --repo /path/to/project --backend codex --trust trusted \
  --task "Fix the median calculation." --verify "python3 -m unittest -q"
```

Only committed source on the selected local branch is delivered. A dirty checkout is refused;
`--ignore-uncommitted` explicitly selects committed state despite local changes. Tags, raw SHAs,
revision expressions, remote-tracking refs and detached HEAD are refused as `--ref` values.

## Inspect the result and approve a rerun

Reports default to `<repo>/../.dca-runs/<run-id>/report.json` and `report.md`; `--out` selects another
directory. A validated nonempty change is imported as `dca/<run-id>` without checking it out or
merging it. Read the report and inspect that branch before deciding how to integrate it.

An ASK action without a grant records an approval request. Rerun the same task and source with
`--approve <request-id>` to authorize that request for the new run only. If the original report used
custom `--out`, also pass `--approval-report /path/to/report.json`. A changed task, source commit,
backend, trust or action target makes the approval stale (exit 3). A later run without `--approve`
does not inherit a grant. DENY actions cannot be approved. See
[quickstart §6](../specs/001-bounded-coding-agent/quickstart.md#6-blocked-run-and-approval-re-run-us3us4-fr-027).

| Exit | Meaning |
| --- | --- |
| 0 | Task succeeded with a validated completion report. |
| 2 | Invalid request or project configuration. |
| 3 | Environmental/source/approval precondition failure; no completion report. |
| 4 | Infrastructure abort; no completion report or partial result branch; cleanup attempted. |
| 10 | Task failed conclusively, with a report. |
| 11 | Task blocked, with a report; this includes ineligible untrusted requests. |

## Verification and benchmarks

`dca verify` performs live environment checks. `sh scripts/verify.sh --static` checks runtime assets
and evidence without a daemon or backend login. From the DCA checkout, run the same deterministic
checks as CI:

```sh
python3 -m pip install -r requirements-dev.txt
sh scripts/verify.sh --static
python3 -W error::ResourceWarning -m unittest discover -s tests -t . -q
python3 tests/oracles/check_seeds.py
```

Test discovery includes the golden oracle suite. To run those oracles separately use
`python3 tests/oracles/run_oracles.py`; agent-produced candidates are instead checked offline in
the pinned container image, never on the host.

`dca bench --backend claude` runs R1–R10. `--fixtures 'F*'` selects the recovery story, and
`--backend both` runs backends sequentially. Each run is a real production `dca run` and uses the
selected backend's subscription and limits. `--trust` selects the benchmark profile; fixture trust
and S5 gate conditions resolve applicability as defined by the
[CLI contract](../specs/001-bounded-coding-agent/contracts/launcher-cli.md).
`--acceptance --repeat 3` requires the complete 28-applicable-fixture protocol, exact pins and
current gate evidence. It is refused while fixture authoring is incomplete.

## Troubleshooting

- **Docker not signed in or unavailable:** run `sbx login`, check Docker Desktop and
  `sbx ls --json`, then rerun `dca verify`. A benchmark cannot start without a readable baseline;
  if its final sandbox query fails, cleanup is `unknown` and the run fails.
- **Version mismatch:** restore the recorded versions, or deliberately upgrade and repeat the
  affected gates and production conformance. `--allow-drift` is diagnostic and cannot make a run
  acceptance-eligible.
- **Unavailable backend:** use `dca verify` and the selected backend's sign-in flow. Historical
  eligibility does not guarantee the provider will accept credentials today.
- **Policy fingerprint mismatch:** inspect the intentional policy change and renew G4 plus
  production conformance. The launcher does not widen or reset global policy.
- **Blocked task:** read `primary_reason`, verification evidence and the required human action in
  the report. Supply the named prerequisite or a provenance-bound approval before rerunning.
