"""Backend-neutral policy gate (tasks.md T030, contracts/policy-gate.md).

Standard library only. Invoked as a process with the hook payload on stdin:
  /usr/bin/env -i PATH=/usr/bin:/bin LANG=C.UTF-8 /usr/bin/python3 -I /opt/dca/lib/dca/policy_gate.py

THE ONE RULE EVERYTHING ELSE SERVES: no code path exits 0 without a decision. Claude reads exit 0
as allow; Codex needs exit 0 AND the snake_case decision on stdout, because an exit 0 with no
decision falls through to confirmation and `--exec --json` rejects it. Every other outcome -
unknown agent, malformed payload, unreadable policy, internal exception - is exit 2. The module
therefore wraps everything in one catch-all that denies.

TRUSTED-ROOT BOOTSTRAP. The trusted root is derived ONLY from this file's own resolved absolute
path, never from the environment, the payload or the current directory, and sys.path is replaced
with that root plus the interpreter's stdlib entries. A repository can set PYTHONPATH, drop a
`shellparse.py` in the working directory, or point PYTHONHOME somewhere hostile; none of it can
change what this imports. The same derivation gives the kit dir and the run-state root, so a
staged copy under a temporary prefix behaves identically to production without reading any
override - which is what makes T031 able to test the real thing.

CLASS 26 IS NORMATIVELY DENY. An allowed delegation or skill load is its POSITIVE COMPLEMENT: it is
outside class 26 and is logged as `class: null` with `rule: class26-positive-complement`, never as
`class: 26` with `decision: allow`. The skill NAME is never sufficient - the copy under the trusted
skill root must match the canonical kit manifest - so an invalid manifest denies every skill load.

CONTEXT RECORD FIRST (FR-001, T081). Until a valid Context Record exists at <run>/out/context.json,
a call that would change the workspace is refused: a shell command that is not provably inspection
(a build, test or baseline run counts, data-model.md), and a file-writing tool aimed outside the
scratch dir. Reads, git inspection and writes to the scratch dir stay open, so the agent can
research and write the record. The refusal carries the class the call would otherwise have (8 for
shell, 3 for file writes): the action classes stay exactly 1-31. Both backends reach it through the
same normalize(), and shell commands are judged by the same shellparse.command_effect the host's
FR-001 detector uses.

This gate is a COOPERATIVE control. The agent has sudo in the VM and can alter it. The enforcement
boundary is host-side: the microVM, the sbx network policy, the credential proxy, host limits and
host-authoritative grants.
"""

import json
import os
import re
import sys

# --- trusted-root bootstrap, before any project import ---------------------------------------------

_SELF = os.path.realpath(__file__)                     # <prefix>/opt/dca/lib/dca/policy_gate.py
_TRUSTED_ROOT = os.path.dirname(os.path.dirname(_SELF))          # <prefix>/opt/dca/lib
_KIT_DIR = os.path.dirname(_TRUSTED_ROOT)                        # <prefix>/opt/dca
_PREFIX = os.path.dirname(os.path.dirname(_KIT_DIR)) or "/"      # <prefix>
_RUN_DIR = os.path.join(_PREFIX, "run", "dca")

# stdlib entries only, plus the trusted root. Anything the caller put on sys.path is discarded,
# and site-packages is excluded: the contract says stdlib entries only, so a third-party package
# must not be importable from inside the gate.
_STDLIB = [p for p in sys.path
           if p and os.path.isdir(p)
           and ("python" in p.lower() or "lib-dynload" in p)
           and "site-packages" not in p and "dist-packages" not in p]
sys.path[:] = [_TRUSTED_ROOT] + _STDLIB

EXIT_ALLOW, EXIT_DENY = 0, 2
CODEX_ALLOW = {"hook_specific_output": {"hook_event_name": "pre_tool_use",
                                        "permission_decision": "allow"}}

RUNTIME_SKILLS = ("repository-navigation", "root-cause-debugging", "verification",
                  "change-receipt")
SUBAGENTS = {"dca-researcher": "researcher", "dca-reviewer": "reviewer"}
ROLES = ("root", "researcher", "reviewer")

_HEX64 = re.compile(r"\A[0-9a-f]{64}\Z")
_URL = re.compile(r"https?://([^/\s\"']+)", re.I)


class Deny(Exception):
    """A decision to refuse. `action_class` None means an internal failure."""

    def __init__(self, action_class, reason, request=None):
        super().__init__(reason)
        self.action_class = action_class
        self.reason = reason
        self.request = request


# --- inputs ---------------------------------------------------------------------------------------

def _read_json(path):
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def _no_duplicate_keys(pairs):
    seen = {}
    for key, value in pairs:
        if key in seen:
            raise ValueError(f"duplicate key {key!r}")
        seen[key] = value
    return seen


def load_policy():
    return _read_json(os.path.join(_KIT_DIR, "policy", "actions.yaml"))


def load_run():
    return _read_json(os.path.join(_RUN_DIR, "run.json"))


def load_grants():
    try:
        document = _read_json(os.path.join(_RUN_DIR, "grants.json"))
    except (OSError, ValueError):
        return []
    grants = document.get("grants") if isinstance(document, dict) else None
    return grants if isinstance(grants, list) else []


def normalize(payload):
    """The backends' native payloads, reduced to the contract's shape."""
    if not isinstance(payload, dict):
        raise Deny(None, "payload is not an object")
    tool = payload.get("tool_name")
    if not isinstance(tool, str) or not tool:
        raise Deny(None, "payload has no tool name")
    tool_input = payload.get("tool_input")
    if not isinstance(tool_input, dict):
        tool_input = {}

    # The backend is decided by the hook event name, NOT by whether agent_name happens to be
    # present. Branching on presence let a Codex payload that omitted agent_name fall through to
    # the Claude path and be treated as root, which is exactly the denial the contract requires.
    event = payload.get("hook_event_name")
    if event == "pre_tool_use":
        backend = "codex"
        agent = payload.get("agent_name")
        if agent not in ROLES:
            raise Deny(None, f"missing or unknown codex agent_name {agent!r}")
    elif event == "PreToolUse" or "agent_type" in payload or event is None:
        backend = "claude"
        agent_type = payload.get("agent_type")
        if agent_type in (None, ""):
            agent = "root"
        elif agent_type in SUBAGENTS:
            agent = SUBAGENTS[agent_type]
        else:
            raise Deny(None, f"unknown claude agent_type {agent_type!r}")
    else:
        raise Deny(None, f"unknown hook event {event!r}")

    return {"backend": backend, "agent": agent, "tool": tool, "input": tool_input,
            "cwd": payload.get("cwd") or "", "session_id": payload.get("session_id") or ""}


# --- kit manifest ----------------------------------------------------------------------------------

def verify_skill(name):
    """True only when `name` is a runtime skill whose kit copy matches the canonical manifest.

    An invalid manifest is an unverifiable source, so it denies EVERY skill load rather than the
    one being asked for. The name alone is never sufficient.
    """
    if name not in RUNTIME_SKILLS:
        return False
    try:
        with open(os.path.join(_KIT_DIR, "kit-manifest.json"), "rb") as fh:
            raw = fh.read()
        document = json.loads(raw.decode("utf-8"), object_pairs_hook=_no_duplicate_keys)
    except (OSError, ValueError, UnicodeDecodeError):
        return False

    if not isinstance(document, dict) or set(document) != {"manifest_version", "skills"}:
        return False
    if document["manifest_version"] != 1:
        return False
    skills = document["skills"]
    if not isinstance(skills, dict) or set(skills) != set(RUNTIME_SKILLS):
        return False

    import hashlib
    for key, entry in skills.items():
        if not isinstance(entry, dict) or set(entry) != {"path", "sha256"}:
            return False
        if entry["path"] != f"skills/{key}/SKILL.md":
            return False
        if not isinstance(entry["sha256"], str) or not _HEX64.match(entry["sha256"]):
            return False

    # No unlisted file or directory may sit under the trusted skill root.
    skills_root = os.path.join(_KIT_DIR, "skills")
    try:
        present = set(os.listdir(skills_root))
    except OSError:
        return False
    if present != set(RUNTIME_SKILLS):
        return False

    entry = skills[name]
    target = os.path.join(_KIT_DIR, entry["path"])
    try:
        with open(target, "rb") as fh:
            digest = hashlib.sha256(fh.read()).hexdigest()
    except OSError:
        return False
    return digest == entry["sha256"]


# --- path classification -----------------------------------------------------------------------------

def _match_glob(path, pattern):
    import fnmatch
    if fnmatch.fnmatch(path, pattern):
        return True
    # `**/x` should also match a bare `x` at the root of the tree
    if pattern.startswith("**/") and fnmatch.fnmatch(path, pattern[3:]):
        return True
    return False


def classify_path(path, run, policy, writing):
    """Class 2, 5, 6, 1/3 for one path argument, decided on its realpath."""
    resolved = os.path.realpath(os.path.join(run["workspace"], path)) if not os.path.isabs(path) \
        else os.path.realpath(path)
    workspace = os.path.realpath(run["workspace"])
    scratch = os.path.realpath(run["scratch"])

    inside_workspace = resolved == workspace or resolved.startswith(workspace + os.sep)
    inside_scratch = resolved == scratch or resolved.startswith(scratch + os.sep)

    if inside_scratch:
        return 6, resolved
    for pattern in policy["policy"]["sensitive_globs"]:
        if _match_glob(resolved, pattern):
            return 2, resolved
    if not inside_workspace:
        return 5, resolved
    return (3 if writing else 1), resolved


# --- network intent ------------------------------------------------------------------------------------

BROWSE_TOOLS = frozenset({"websearch", "webfetch", "web_search", "web_fetch", "fetch",
                          "open_url", "browser", "browse"})
HTTP_CLIENTS = frozenset({"curl", "wget", "http", "https", "nc"})


def _hosts_in(text):
    return [m.group(1).split(":")[0].lower() for m in _URL.finditer(text or "")]


def classify_network(entry, policy, shellparse):
    """Class 21, 30 or 31 when the call has network intent, else None."""
    doc_hosts = {h.lower() for h in policy["policy"]["documentation_hosts"]}
    tool = entry["tool"].lower()
    blob = json.dumps(entry["input"])

    if tool in BROWSE_TOOLS:
        hosts = _hosts_in(blob)
        if hosts and all(h in doc_hosts for h in hosts):
            return 31, hosts[0]
        return 30, (hosts[0] if hosts else tool)

    command = entry["input"].get("command") or entry["input"].get("cmd")
    if isinstance(command, str) and command.strip():
        parsed = shellparse.parse(command)
        if not parsed.ok:
            return None            # class 28 is decided by the caller
        programs = {(s.program or "").rsplit("/", 1)[-1] for s in parsed.segments}
        if programs & HTTP_CLIENTS or _hosts_in(command):
            hosts = _hosts_in(command)
            if hosts and all(h in doc_hosts for h in hosts):
                return 31, hosts[0]
            if programs & HTTP_CLIENTS:
                return 21, (hosts[0] if hosts else "unknown-host")
    return None


# --- decision ---------------------------------------------------------------------------------------------

MUTATING = frozenset({"write", "edit", "notebookedit", "write_file", "edit_file", "create_file",
                      "delete_file", "move_file", "bash", "shell", "run_command", "transfer_task",
                      "apply_patch"})
READ_ONLY_EXEMPT = frozenset({"git_diff", "git_status", "git_log", "read", "grep", "glob",
                              "read_file", "list_directory", "search"})
DELEGATION_TOOLS = frozenset({"transfer_task", "task", "agent"})
#: Shell tools (the host detector's list): a command here is inspection, scratch-only or a change.
SHELL_TOOLS = frozenset({"bash", "sh", "shell", "run_command", "run_shell_command", "execute",
                         "exec", "terminal"})
#: Tools that write files. Aimed outside the scratch dir, a call by one of them is a change.
WORKSPACE_WRITERS = frozenset({"write", "edit", "multiedit", "notebookedit", "write_file",
                               "edit_file", "create_file", "delete_file", "move_file",
                               "apply_patch", "create_directory", "remove_directory"})
_MAX_RECORD_BYTES = 1_000_000
SKILL_TOOLS = frozenset({"read_skill", "read_skill_file", "skill"})


def _record_problem(record):
    """Why `record` is not a Context Record (FR-001), or None. The same test the host applies to the
    retrieved record (dca.bench._context_record_problem)."""
    if not isinstance(record, dict):
        return "it is not a JSON object"
    classification = record.get("classification")
    if not isinstance(classification, dict) or classification.get("value") not in ("direct", "planned") \
            or not str(classification.get("reason") or "").strip():
        return "it has no classification with a reason"
    if not isinstance(record.get("repository_map"), dict):
        return "it has no repository_map"
    approach = record.get("verification_approach")
    if not isinstance(approach, dict) or approach.get("type") not in ("deterministic", "alternative"):
        return "it has no verification_approach"
    return None


def context_record_problem():
    """None when a valid Context Record exists; otherwise why it does not.

    Anything the gate cannot establish - no file, a directory, an unreadable or oversized file,
    invalid JSON, a duplicate key - is "no record": it never reads as satisfied.
    """
    path = os.path.join(_RUN_DIR, "out", "context.json")
    try:
        with open(path, encoding="utf-8") as fh:
            raw = fh.read(_MAX_RECORD_BYTES + 1)
    except FileNotFoundError:
        return "none has been written yet"
    except (OSError, ValueError) as exc:
        return f"it cannot be read ({type(exc).__name__})"
    if len(raw) > _MAX_RECORD_BYTES:
        return "it is too large"
    try:
        record = json.loads(raw, object_pairs_hook=_no_duplicate_keys)
    except ValueError:
        return "it is not valid JSON"
    return _record_problem(record)


def workspace_change_class(entry, shellparse, scratch):
    """The action class of a call FR-001 counts as a workspace change, or None for one that is not.

    Shell: 8 unless every segment is inspection or a scratch-only write (28 for a command that does
    not parse); a shell call whose command the gate cannot read is a change. File writers: 3 unless every target is inside `scratch`, the
    run's scratch dir (run.json, the same source class 6 uses).
    Every other tool - reads, git inspection, delegation, skills - changes nothing.
    """
    lowered = entry["tool"].lower()
    if lowered in SHELL_TOOLS:
        command = shellparse._command_text(entry["input"])
        if command is None:
            return 8
        if not shellparse.parse(command).ok:
            return 28       # the class it would otherwise have; the ordering refusal opens no request
        return 8 if shellparse.command_effect(command, scratch)[0] == "mutate" else None
    if lowered in WORKSPACE_WRITERS:
        paths = shellparse._target_paths(entry["input"])
        if paths and all(shellparse._under_scratch(path, scratch) for path in paths):
            return None
        return 3
    return None


def decide(entry, run, policy, grants, shellparse):
    """(action_class or None, rule or None, target). Raises Deny to refuse."""
    tool = entry["tool"]
    lowered = tool.lower()
    agent = entry["agent"]
    trust = run.get("trust_level", "untrusted")
    decisions = {c["id"]: c for c in policy["classes"]}

    # --- class 29: the advisory step limit outranks everything else --------------------------
    if run.get("step_limit") is not None and run.get("steps_used", 0) >= run["step_limit"]:
        raise Deny(29, "DCA_LIMIT steps: stop now and write the completion report with "
                       "outcome blocked")

    # --- class 26 and its positive complement -------------------------------------------------
    if lowered == "run_skill":
        raise Deny(26, "run_skill is never on the runtime allowlist")

    if lowered in DELEGATION_TOOLS:
        target = (entry["input"].get("agent") or entry["input"].get("subagent_type")
                  or entry["input"].get("name") or "")
        role = SUBAGENTS.get(target, target)
        if agent != "root":
            raise Deny(26, f"{agent} may not delegate")
        if role not in ("researcher", "reviewer"):
            raise Deny(26, f"subagent {target!r} is not in the runtime allowlist")
        return None, "class26-positive-complement", role

    if lowered in SKILL_TOOLS:
        name = entry["input"].get("name") or entry["input"].get("skill") or ""
        if agent != "root":
            raise Deny(26, f"{agent} may not load skills")
        if not verify_skill(name):
            raise Deny(26, f"skill {name!r} has no verifiable trusted source")
        return None, "class26-positive-complement", name

    # --- class 27: read-only roles ---------------------------------------------------------------
    if agent in ("researcher", "reviewer") and lowered in MUTATING \
            and lowered not in READ_ONLY_EXEMPT:
        raise Deny(27, f"{agent} may not use the mutating tool {tool}")

    # --- network intent -------------------------------------------------------------------------
    network = classify_network(entry, policy, shellparse)
    if network is not None:
        action_class, target = network
        decision = decisions[action_class]["decision"][trust]
        if decision == "DENY":
            raise Deny(action_class, f"{decisions[action_class]['name']}: {target}")
        if decision == "ASK":
            _require_grant(action_class, target, grants, decisions)
        return action_class, None, target

    # --- Context Record first (FR-001) ---------------------------------------------------------
    change_class = workspace_change_class(
        entry, shellparse,
        run.get("scratch") if isinstance(run.get("scratch"), str) and run.get("scratch") else shellparse.SCRATCH_DIR)
    if change_class is not None:
        problem = context_record_problem()
        if problem is not None:
            raise Deny(change_class,
                       "Context Record is required before verification may run or any file "
                       "changes: write /run/dca/out/context.json (classification, repository_map, "
                       f"verification_approach) first - {problem}")

    # --- shell parseability ----------------------------------------------------------------------
    command = entry["input"].get("command") or entry["input"].get("cmd")
    if isinstance(command, str) and command.strip():
        parsed = shellparse.parse(command)
        if not parsed.ok:
            _require_grant(28, command[:80], grants, decisions)
            return 28, None, command[:80]
        declared = set(run.get("verification_commands") or [])
        if command.strip() in declared:
            return 8, None, command.strip()

    # --- path classification ------------------------------------------------------------------------
    path = (entry["input"].get("file_path") or entry["input"].get("path")
            or entry["input"].get("notebook_path"))
    if isinstance(path, str) and path:
        writing = lowered in MUTATING
        action_class, resolved = classify_path(path, run, policy, writing)
        decision = decisions[action_class]["decision"][trust]
        if decision == "DENY":
            raise Deny(action_class, f"{decisions[action_class]['name']}: {resolved}")
        if decision == "ASK":
            _require_grant(action_class, resolved, grants, decisions)
        return action_class, None, resolved

    # Nothing matched a stricter class: an ordinary in-workspace tool call.
    return 1, None, tool


def _require_grant(action_class, target, grants, decisions):
    """ASK becomes ALLOW only with a matching grant. A DENY class is never grantable."""
    if decisions[action_class]["decision"]["trusted"] == "DENY":
        raise Deny(action_class, "a DENY class is never grantable")
    for grant in grants:
        if not isinstance(grant, dict):
            continue
        if grant.get("action_class") == action_class \
                and grant.get("normalized_target") == target \
                and grant.get("granted_by") == "developer-cli":
            return
    raise Deny(action_class, f"{decisions[action_class]['name']}: {target}",
               request={"action_class": action_class, "normalized_target": target,
                        "reason": decisions[action_class]["name"]})


# --- state ---------------------------------------------------------------------------------------------------

def _append(path, record):
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, sort_keys=True) + "\n")
    except OSError:
        pass            # the log is advisory; failing to write it must not change the decision


def log_decision(entry, run, action_class, rule, decision, request_id=None):
    record = {
        "ts": __import__("datetime").datetime.now(
            __import__("datetime").timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "agent": entry["agent"] if entry else None,
        "tool": entry["tool"] if entry else None,
        "class": action_class,
        "decision": decision,
        "counters": {"steps_used": (run or {}).get("steps_used", 0),
                     "retries_used": (run or {}).get("retries_used", 0)},
    }
    if rule:
        record["rule"] = rule
    if request_id:
        record["request_id"] = request_id
    _append(os.path.join(_RUN_DIR, "state", "gate.log.jsonl"), record)


def record_request(run, request):
    path = os.path.join(_RUN_DIR, "state", "approvals.jsonl")
    try:
        existing = sum(1 for _ in open(path, encoding="utf-8"))
    except OSError:
        existing = 0
    identifier = f"apr-{run.get('run_id', 'unknown')}-{existing + 1}"
    record = dict(request, id=identifier, run_id=run.get("run_id"),
                  trust_level=run.get("trust_level"), status="requested", risk="medium")
    _append(path, record)
    return identifier


# --- entry point -------------------------------------------------------------------------------------------------

def main(argv=None):
    entry = run = None
    try:
        raw = sys.stdin.read()
        try:
            payload = json.loads(raw)
        except ValueError:
            raise Deny(None, "payload is not valid JSON")

        entry = normalize(payload)
        run = load_run()
        policy = load_policy()
        grants = load_grants()
        from dca import shellparse                            # from the trusted root only

        action_class, rule, _target = decide(entry, run, policy, grants, shellparse)
    except Deny as refusal:
        request_id = None
        if refusal.request is not None and run is not None:
            try:
                request_id = record_request(run, refusal.request)
            except Exception:                                 # noqa: BLE001
                request_id = None
        log_decision(entry, run, refusal.action_class, None, "deny", request_id)
        if request_id:
            sys.stderr.write(
                f"DCA_APPROVAL_REQUIRED {request_id}: {refusal.reason}; risk=medium\n")
        elif refusal.action_class == 29:
            sys.stderr.write(f"{refusal.reason}\n")
        else:
            sys.stderr.write(f"DCA_DENY {refusal.action_class}: {refusal.reason}\n")
        return EXIT_DENY
    except Exception as exc:                                  # noqa: BLE001 - never exit 0 on error
        log_decision(entry, run, None, None, "deny")
        sys.stderr.write(f"DCA_DENY internal: {type(exc).__name__}\n")
        return EXIT_DENY

    log_decision(entry, run, action_class, rule, "allow")
    if entry["backend"] == "codex":
        sys.stdout.write(json.dumps(CODEX_ALLOW) + "\n")
    return EXIT_ALLOW


if __name__ == "__main__":
    sys.exit(main())
