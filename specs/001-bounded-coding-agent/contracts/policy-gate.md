# Contract: policy gate

A single executable, `/opt/dca/bin/dca-gate`, is installed in the VM by the kit. It is a thin
POSIX `sh` wrapper around `/opt/dca/lib/dca/policy_gate.py`. Any internal error, including a
missing interpreter or module, results in **exit 2 (deny)**.

**Invocation invariants** (both backends evaluate the same `actions.yaml` with the same code):
- **Claude**: the managed PreToolUse hook runs `dca-gate` before every tool call.
- **Codex**: every Codex tool call that is not already rejected by an equal-or-stricter native
  DENY must pass through `dca-gate` before it can execute. Native `permissions.deny` rules may
  reject a prohibited call **before** the gate; that is acceptable because they are at least as
  strict, and they are kept as defense in depth. This invariant holds only in safety mode
  `strict`, which the launcher pins with an explicit `--safety strict` (research R20, E18).

**Environment hardening.** The wrapper doesn't rely on repository-controlled environment for its
interpreter or module resolution, because a repository's `.claude/settings.json` `env` block can
still inject variables into the hook runner's environment.

- **Invocation**: the wrapper runs the gate as a **child process, without `exec`**:
  `/usr/bin/env -i PATH=/usr/bin:/bin LANG=C.UTF-8 /usr/bin/python3 -I /opt/dca/lib/dca/policy_gate.py`,
  with the hook payload on stdin.
- **Status mapping**: status 0 stays 0; status 2 stays 2; **every other status, including 126 or
  127 from a missing or non-executable interpreter, becomes 2**. `exec` is not used, because it
  would replace the wrapper and let such a status escape unmapped, and Claude treats a non-2
  non-zero exit as non-blocking.
- **Absolute paths**: the interpreter and gate module are named by absolute path.
- **Minimal environment**: `env -i` starts from an empty environment with an explicit minimal `PATH`. This removes `PYTHONPATH`, `PYTHONHOME`, `PYTHONSTARTUP`, `PYTHONUSERBASE`, `LD_PRELOAD`, `LD_LIBRARY_PATH`, `BASH_ENV`, `ENV` and every other inherited variable.
- **Isolated interpreter**: Python's `-I` ignores `PYTHON*` variables, user site-packages and the script-directory path entry.
- **Trusted-root imports**: the gate derives its trusted root (`/opt/dca/lib`) only from its own resolved absolute path and sets `sys.path` to that root plus the interpreter's stdlib entries, so the current directory, `PYTHONPATH` and `PYTHONHOME` can't influence imports. An import failure exits 2.
- **Inputs**: the gate reads its inputs only from stdin (the hook payload), from `/run/dca/*`, and from read-only kit files under its own trusted root: `/opt/dca/policy/*`, and the trusted skill root `/opt/dca/skills` with the canonical kit manifest `/opt/dca/kit-manifest.json`, used for the skill-source check.
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
| Codex | `pre_tool_use` on every agent (`matcher: "*"`, `on_error: block`); `on_agent_switch` and `subagent_stop` for fingerprints | `runtime/agents/codex.yaml`, with `safety: strict` on every agent **and** the launcher's explicit `--safety strict` on every execution. `codex.yaml` has top-level `permissions.deny` only (no `allow` or `ask` rules), and the kit's in-VM Docker Agent user config has no `permissions`, `safety`, `yolo` or alias options (verified at preflight), because a custom allow rule would run a call without the gate |

## Input (stdin, JSON)

Each backend's native hook payload. The gate normalizes it to:

```json
{"backend":"claude|codex","agent":"root|researcher|reviewer","tool":"<name>",
 "input":{...},"cwd":"<path>","session_id":"<id>"}
```

The Claude agent comes from `agent_type` (`dca-researcher` → researcher, `dca-reviewer` →
reviewer, absent → root). The Codex agent comes from the hook input's `agent_name`
(`root`, `researcher`, `reviewer`); a missing or unknown name is treated as a denial.

## Configuration (in-VM copies; cooperative)

- `/opt/dca/policy/actions.yaml`, `network.yaml`, `limits.yaml`: copies of `runtime/policy/*`.
- `/opt/dca/skills/<name>/SKILL.md` and the canonical kit manifest `/opt/dca/kit-manifest.json` (see *Kit manifest*): the **trusted runtime skill root** (`<KIT_DIR>/skills`, where `<KIT_DIR>` = `/opt/dca`), holding exactly the four runtime skills. Codex runs receive `DOCKER_AGENT_KIT_DIR=/opt/dca`, so Docker Agent discovers skills only there.
- `/run/dca/run.json`: run id, trust level, classification in force, workspace path, scratch path, required verification commands.
- `/run/dca/grants.json`: an in-VM **copy** of the run's grants. The authoritative copy is on the host. Network grants take effect only through the host-applied sbx policy, and reported approvals are reconciled against the host copy.
- `/run/dca/state/`: advisory counters and the decision log. Writes to it by the agent are cooperatively denied (class 25), but it is not tamper-proof.
- `/run/dca/out/`: the run scratch dir (class 6) where the agent writes `context.json` (the Context Record), `plan.md` and `report.agent.json`. The gate reads the Context Record's presence and shape for the FR-001 ordering rule (see *Classification rules*), and its scope set only to classify class 4 cooperatively. Agent-written files never create grants, never change host policy or limits, and are never provenance.

## Output

- **ALLOW**: exit 0. For Claude, exit 0 with no output. For Codex, exit 0 **with** the decision on stdout: `{"hook_specific_output":{"hook_event_name":"pre_tool_use","permission_decision":"allow"}}`. Docker Agent reads only this snake_case form. An exit 0 with no decision means "no decision", which in `strict` mode falls through to confirmation and is rejected by `--exec --json` (fail closed).
- **DENY**: exit 2; stderr `DCA_DENY <class>: <reason>`.
- **ASK without grant**: exit 2; stderr `DCA_APPROVAL_REQUIRED <request-id>: <action> on <target> — <reason>; risk=<risk>`. The request is appended to `/run/dca/state/approvals.jsonl`.
- **Advisory limit reached**: exit 2; stderr `DCA_LIMIT <steps|retries>: stop now and write the completion report with outcome blocked`. One report-writing call to the run scratch path is still allowed. This is an early, cooperative stop; the host enforces the same limits independently.

Every decision is appended to `/run/dca/state/gate.log.jsonl` as
`{ts, agent, tool, class, rule?, decision, request_id?, counters}`. It never records file contents or
secret values.
- **Ordinary decisions**: `class` is the action class (an integer 1–31) and `rule` is absent.
- **Class-26 positive complement** (root → `researcher`/`reviewer`; root → a trusted runtime skill): `class: null`, `rule: "class26-positive-complement"`, `decision: "allow"`. Because action class 26 is normatively DENY, an allowed call is **never** logged as `class: 26` with `decision: "allow"`.
- **Class-26 violations** (any other subagent or skill, an unverifiable skill source, `run_skill`, delegation or skill calls by the researcher or reviewer): `class: 26`, `decision: "deny"`.
- There is no class 0 or 32; action classes remain exactly 1–31.

## Kit manifest (single canonical definition)

- **Path**: `<KIT_DIR>/kit-manifest.json` (`/opt/dca/kit-manifest.json` in production). It is the **only** kit manifest. Claude's kit-installed skill copies are checked against the same entries.
- **Serialization**: UTF-8 JSON. The staging tool writes it in canonical form: sorted keys, `,` and `:` separators with no whitespace, and no trailing newline (Python: `json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)`). Readers parse it strictly and validate the structure below.
- **Structure** (exactly; no additional properties at any level):

  ```json
  {"manifest_version":1,"skills":{"change-receipt":{"path":"skills/change-receipt/SKILL.md","sha256":"<64 lowercase hex>"},"repository-navigation":{"path":"skills/repository-navigation/SKILL.md","sha256":"…"},"root-cause-debugging":{"path":"skills/root-cause-debugging/SKILL.md","sha256":"…"},"verification":{"path":"skills/verification/SKILL.md","sha256":"…"}}}
  ```

  - `manifest_version`: the integer `1`.
  - `skills`: exactly the four keys `repository-navigation`, `root-cause-debugging`, `verification`, `change-receipt`.
  - Each entry has exactly `path` and `sha256`. `path` must equal `skills/<name>/SKILL.md`, relative to `<KIT_DIR>` (no absolute path, no `..`). `sha256` is the SHA-256 of the file's raw bytes, as **64 lowercase hexadecimal characters with no prefix**.
- **Failure behavior**:
  - **Invalid manifest**: the file is missing or unreadable; it isn't valid UTF-8 JSON; any object has a duplicate key; `manifest_version` isn't `1`; a skill key is missing or extra; an entry has a missing, extra or wrongly typed property; a `path` differs from `skills/<name>/SKILL.md`; or a `sha256` isn't 64 lowercase hex characters.
  - **Hash mismatch**: the file at `<KIT_DIR>/<path>` is missing, or its SHA-256 differs from the entry.
  - **Extra entry**: any file or directory under `<KIT_DIR>/skills/` that the manifest doesn't list.
  - Kit install and the in-VM kit/gate preflight **fail** on any of these, so the run doesn't start (launcher-cli Phase 3 step 5: exit 4, provisioning), and production conformance FAILs.
  - At call time the gate treats an invalid manifest, or a mismatched file for the requested skill, as an unverifiable source: the skill call is class 26 DENY (exit 2), never ALLOW. An invalid manifest therefore denies every skill load.

## Classification rules

- **Parity**: both backends evaluate the same `actions.yaml` with the same code. Each action class has exactly one decision per trust level (data-model Action Classes 1–31).
- **Delegation and skills** (positive complement of class 26; no new class): ALLOW only for **root** delegating to `researcher` or `reviewer` (Codex `transfer_task`; Claude's subagent tool for `dca-researcher` or `dca-reviewer`), and for **root** loading one of the four runtime skills from the trusted source. For Codex that means `read_skill`/`read_skill_file` for a name whose `<KIT_DIR>/skills/<name>/SKILL.md` matches its entry in the canonical kit manifest (*Kit manifest*), where `<KIT_DIR>` is derived from the gate's own trusted root. For Claude, the G1d-proven source, or the G1d fallback's source and hash check. Everything else is class 26 DENY: any other subagent or skill, an unverifiable source (the name alone is never enough), `run_skill`, and any delegation or skill call by the researcher or reviewer.
- **Context Record first** (FR-001; no new class): until a valid Context Record (`classification` with a value and a reason, a `repository_map`, and a `verification_approach` of type `deterministic` or `alternative`; the same test the host applies to the retrieved record) exists at `/run/dca/out/context.json`, the gate refuses a call that changes the workspace. That is a shell command that is not provably inspection, so a baseline, test or build run counts (data-model *First workspace mutation*), and a file-writing tool aimed outside the run scratch dir. The refusal is exit 2, `DCA_DENY <class>: Context Record is required before verification may run ...`, under the class the call would otherwise have (8 for shell, 3 for file writes, 28 for a command that does not parse), and it opens no approval request. Reads, git inspection, delegation, skill loads and writes confined to the scratch dir stay allowed, so the agent can research and write the record. A record the gate cannot read, parse or validate counts as absent (fail closed). Shell commands are judged by the same `shellparse.command_effect` the host's FR-001 detector uses. This is a cooperative control like the rest of the gate; the host's ordering check, which counts a refused attempt as an attempt, stays authoritative.
- **Paths**: every path argument is resolved with `realpath` before classification. Escaping the workspace or scratch dir is class 5 (DENY).
- **Shell**: commands are split into segments at `;`, `&&`, `||`, `|` and newlines. `$(…)`, backticks, `eval`, `sh -c` and `bash -c` are treated recursively or conservatively. Anything unparseable is class 28 (ASK).
- **Network intent** (destinations are enforced by the host sbx policy):
  - a shell HTTP client or language one-liner to a host outside the policy lists → class 21, **external API call (ASK)**;
  - a web-search tool, or a browsing/fetch tool on a non-policy-listed page → class 30, **unconstrained general web browsing (DENY)**;
  - a policy-listed documentation host → class 31 (trusted ALLOW, untrusted ASK).
