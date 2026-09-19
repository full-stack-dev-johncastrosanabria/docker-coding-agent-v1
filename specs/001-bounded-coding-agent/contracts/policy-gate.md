# Contract: policy gate

A single executable, `/opt/dca/bin/dca-gate`, is installed in the VM by the kit and invoked
before every tool call on both backends. It is a thin POSIX `sh` wrapper around
`/opt/dca/lib/dca/policy_gate.py`. Any internal error, including a missing interpreter, results in
**exit 2 (deny)**.

**Environment hardening.** The wrapper doesn't rely on repository-controlled environment for its
interpreter or module resolution, because a repository's `.claude/settings.json` `env` block can
still inject variables into the hook runner's environment.

- **Invocation**: it runs the gate as
  `exec /usr/bin/env -i PATH=/usr/bin:/bin LANG=C.UTF-8 /usr/bin/python3 -I /opt/dca/lib/dca/policy_gate.py`.
- **Absolute paths**: the interpreter and gate module are named by absolute path.
- **Minimal environment**: `env -i` starts from an empty environment with an explicit minimal `PATH`. This removes `PYTHONPATH`, `PYTHONHOME`, `PYTHONSTARTUP`, `PYTHONUSERBASE`, `LD_PRELOAD`, `LD_LIBRARY_PATH`, `BASH_ENV`, `ENV` and every other inherited variable.
- **Isolated interpreter**: Python's `-I` ignores `PYTHON*` variables, user site-packages and the script-directory path entry.
- **Inputs**: the gate reads its inputs only from stdin (the hook payload) and from `/run/dca/*`.
- **Pinned variables**: managed settings also pin `BASH_ENV`, `ENV`, `LD_PRELOAD`, `LD_LIBRARY_PATH`, `PYTHONPATH`, `PYTHONHOME` and `PYTHONSTARTUP`, so they can't affect the shell that launches the wrapper.
- **Evidence**: G1c tests this with a repository `env` that sets those variables.

The gate is a **cooperative, in-VM control**: it stops accidental and model-driven violations
early and records intent. It is **not** the security boundary. The agent has sudo inside the VM,
so the gate, its configuration and its state can be altered by a determined in-VM process. The
enforcement boundary is host-side:

- the microVM;
- no host mounts (sanitized committed source);
- the sbx network policy;
- the credential proxy;
- host-enforced limits;
- host-authoritative grants;
- change retrieval by bundle.

## Invocation

| Backend | Hook | Registered in |
|---|---|---|
| Claude | `PreToolUse` (all tools), plus `SubagentStart`/`SubagentStop` for fingerprints | VM **managed** settings, with `allowManagedHooksOnly: true` |
| Codex | `pre_tool_use` with `on_error: block`; `on_agent_switch` and `subagent_stop` for fingerprints | `runtime/agents/codex.yaml` |

## Input (stdin, JSON)

Each backend's native hook payload. The gate normalizes it to:

```json
{"backend":"claude|codex","agent":"root|researcher|reviewer","tool":"<name>",
 "input":{...},"cwd":"<path>","session_id":"<id>"}
```

The Claude agent comes from `agent_type` (`dca-researcher` → researcher, `dca-reviewer` →
reviewer, absent → root). The Codex agent comes from the active agent name.

## Configuration (in-VM copies; cooperative)

- `/opt/dca/policy/actions.yaml`, `network.yaml`, `limits.yaml`: copies of `runtime/policy/*`.
- `/run/dca/run.json`: run id, trust level, classification in force, workspace path, scratch path, required verification commands.
- `/run/dca/grants.json`: an in-VM **copy** of the run's grants. The authoritative copy is on the host. Network grants take effect only through the host-applied sbx policy, and reported approvals are reconciled against the host copy.
- `/run/dca/state/`: advisory counters and the decision log. Writes to it by the agent are cooperatively denied (class 25), but it is not tamper-proof.

## Output

- **ALLOW**: exit 0 (Codex: `permissionDecision: allow`).
- **DENY**: exit 2; stderr `DCA_DENY <class>: <reason>`.
- **ASK without grant**: exit 2; stderr `DCA_APPROVAL_REQUIRED <request-id>: <action> on <target> — <reason>; risk=<risk>`. The request is appended to `/run/dca/state/approvals.jsonl`.
- **Advisory limit reached**: exit 2; stderr `DCA_LIMIT <steps|retries>: stop now and write the completion report with outcome blocked`. One report-writing call to the run scratch path is still allowed. This is an early, cooperative stop; the host enforces the same limits independently.

Every decision is appended to `/run/dca/state/gate.log.jsonl` as
`{ts, agent, tool, class, decision, request_id?, counters}`. It never records file contents or
secret values.

## Classification rules

- **Parity**: both backends evaluate the same `actions.yaml` with the same code. Each action class has exactly one decision per trust level (data-model Action Classes 1–31).
- **Paths**: every path argument is resolved with `realpath` before classification. Escaping the workspace or scratch dir is class 5 (DENY).
- **Shell**: commands are split into segments at `;`, `&&`, `||`, `|` and newlines. `$(…)`, backticks, `eval`, `sh -c` and `bash -c` are treated recursively or conservatively. Anything unparseable is class 28 (ASK).
- **Network intent** (destinations are enforced by the host sbx policy):
  - a shell HTTP client or language one-liner to a host outside the policy lists → class 21, **external API call (ASK)**;
  - a web-search tool, or a browsing/fetch tool on a non-policy-listed page → class 30, **unconstrained general web browsing (DENY)**;
  - a policy-listed documentation host → class 31 (trusted ALLOW, untrusted ASK).
