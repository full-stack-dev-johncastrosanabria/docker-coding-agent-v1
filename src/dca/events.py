"""Production outer-event parser and host-side run accounting (tasks.md T040; R8, FR-023, FR-023a).

Standard library only. This is the host's ONLY reading of what happened inside the sandbox, and
G11 part A is the evidence it is built on: `gates/G11/probe_parser.py` was the spike, this is the
production module, and the semantics are the ones G11 recorded - not new ones.

THE FOUR RULES THAT ARE NOT NEGOTIABLE, each one proven by a G11 criterion:

1. **Dispatch on the `type` string first, never on shape.** `tool_call`, `partial_tool_call`,
   `tool_call_response`, `hook_blocked` and `tool_call_confirmation` all carry a `tool_call` object
   with the same key set. A parser that recognized a call by its shape would count streaming deltas
   (17 partials for 2 calls in one capture), and would count calls a hook had already blocked.
   Only `tool_call` is a dispatched call (G11 criterion 1).

2. **Payload is never re-read as outer events.** Tool output, shell output and model prose all
   travel INSIDE event fields. This module reads whole lines and never looks inside `response`,
   `content` or arguments for something event-shaped, so a tool that prints convincing Docker Agent
   JSON adds exactly zero events (G11 criterion 2).

3. **Fail closed.** An unrecognized type, an unparseable line, a damaged `tool_call`, a missing
   terminal event or a mid-line cut all mean the host does not understand the stream it is reading.
   The runtime is PINNED, so that is a finding about the run, never a line to step over. Nothing is
   repaired (G11 criterion 3).

4. **The process outcome outranks the stream.** A stream that looks finished can never overrule a
   killed or non-zero agent exit (G11 criterion 4).

NATIVE CEILINGS ARE NOT HOST LIMITS (research R19). The pinned runtime has its own static
defence-in-depth stops: `budget_exceeded`, `max_iterations_reached`, and an `error` event with
`code: loop_detected` (the `max_consecutive_tool_calls` guard). When one of those is the last typed
event of the run, the run stopped deliberately: `stream = complete`, `agent_exit = normal`,
`limit_reached = native_ceiling`. It is a bounded-execution TASK outcome - never an infrastructure
abort (exit 4), never `abnormal`, and never confused with the host's authoritative `steps`,
`retries`, `wall_clock` and `tokens` limits. If a host limit fired first, the host reason wins,
because the host is the authority on direct/planned semantics.
"""

import json
import os

try:
    from . import shellparse
except ImportError:  # loaded by path in tests and in the sandbox
    import importlib.util as _ilu
    import sys as _sys

    shellparse = _sys.modules.get("dca_shellparse")
    if shellparse is None:
        _spec = _ilu.spec_from_file_location(
            "dca_shellparse",
            os.path.join(os.path.dirname(os.path.abspath(__file__)), "shellparse.py"))
        shellparse = _ilu.module_from_spec(_spec)
        _sys.modules["dca_shellparse"] = shellparse
        _spec.loader.exec_module(shellparse)


# --- the pinned runtime's typed outer events ------------------------------------------------------

#: The 19 types G11 part A OBSERVED the pinned docker_agent v1.136.0 emit across this repository's
#: real captures, plus the three native-ceiling types research R19 identified. The set is
#: empirical: the binary's Go string table is grouped by length and interleaves unrelated
#: constants, so it cannot yield a trustworthy list, and documentation is not evidence.
OBSERVED_EVENTS = frozenset({
    "agent_choice",
    "agent_choice_reasoning",
    "agent_info",
    "hook_blocked",
    "hook_finished",
    "hook_started",
    "mcp_init_finished",
    "mcp_init_started",
    "message_added",
    "partial_tool_call",
    "stream_started",
    "stream_stopped",
    "team_info",
    "token_usage",
    "tool_call",
    "tool_call_confirmation",
    "tool_call_response",
    "toolset_info",
    "user_message",
})

#: The runtime's own static ceilings (research R19). `error` is here because `code: loop_detected`
#: arrives on an `error` event; an `error` with any other code is still a recognized type.
NATIVE_CEILING_EVENTS = frozenset({"budget_exceeded", "max_iterations_reached", "error"})

RECOGNIZED_EVENTS = OBSERVED_EVENTS | NATIVE_CEILING_EVENTS

TOOL_CALL_EVENT = "tool_call"
TERMINAL_EVENT = "stream_stopped"
#: Types that carry an ATTEMPTED tool call. They are never counted as steps - a blocked call was
#: not dispatched - but an attempted mutation still means the agent tried to change the workspace,
#: which is what FR-001's ordering rule is about.
ATTEMPT_EVENTS = frozenset({"tool_call", "hook_blocked", "tool_call_confirmation"})

COMPLETE = "complete"
HOST_TERMINATED = "host-terminated"
MALFORMED = "malformed"
TRUNCATED = "truncated"
NONE = "none"

NORMAL = "normal"
HOST_LIMIT = "host-limit"
ABNORMAL = "abnormal"
NOT_STARTED = "not-started"

HOST_LIMIT_REASONS = ("steps", "retries", "wall_clock", "tokens")
NATIVE_CEILING = "native_ceiling"

#: Where the run scratch directory lives inside the VM. Writes confined to it are run EVIDENCE -
#: `context.json`, `plan.md`, `report.agent.json` - and data-model.md is explicit that they are not
#: workspace mutations. Recording the plan cannot be the thing the plan has to precede.
SCRATCH_DIR = "/run/dca/out"
CONTEXT_RECORD = "context.json"
PLAN_FILE = "plan.md"


# --- tool classification --------------------------------------------------------------------------

#: Tool names that cannot change the workspace. Delegation is here because data-model.md lists
#: "delegation to the researcher or reviewer" among the things that are NOT mutations.
READ_ONLY_TOOLS = frozenset({
    "read", "read_file", "readfile", "view", "cat_file",
    "ls", "list", "list_directory", "directory_tree", "get_file_info",
    "glob", "grep", "search", "search_files", "find", "notebookread",
    "todoread", "todowrite", "think",
    "task", "transfer_task", "agent", "delegate",
    "read_skill", "read_skill_file", "skill",
    "git_status", "git_log", "git_diff",
})

#: Tools whose effect depends on their arguments: a shell command may be inspection or a build.
SHELL_TOOLS = frozenset({"bash", "sh", "shell", "run_command", "run_shell_command", "execute",
                         "exec", "terminal"})

#: Programs that only look. Deliberately short: anything absent is treated as a mutation, which is
#: the fail-closed direction for an ordering rule (an early first-mutation can only make the
#: Context Record look late, never make a late one look early).
READ_ONLY_PROGRAMS = frozenset({
    "ls", "cat", "head", "tail", "grep", "egrep", "fgrep", "rg", "ag", "find", "wc", "file",
    "stat", "pwd", "basename", "dirname", "tree", "du", "df", "diff", "cmp", "which", "type",
    "date", "whoami", "readlink", "realpath", "sort", "uniq", "cut", "tr", "nl", "column",
    "md5sum", "sha256sum", "shasum", "true", "false", "test",
})

#: `git` subcommands that only read. `git branch` and `git checkout` are absent on purpose: both
#: change refs or the worktree depending on their arguments.
READ_ONLY_GIT = frozenset({
    "status", "log", "diff", "show", "ls-files", "ls-tree", "rev-parse", "cat-file", "blame",
    "describe", "shortlog", "grep", "rev-list", "for-each-ref", "show-ref",
})


def _arguments(call):
    """`function.arguments` decoded, or None when the runtime did not carry any.

    The Claude backend reports harness tool calls with a name and no arguments, so "no arguments"
    is normal traffic and must not be a parse failure. It does mean the argument-dependent checks
    below cannot run, and every one of them then falls back to the conservative answer.
    """
    function = call.get("function")
    if not isinstance(function, dict):
        return None
    raw = function.get("arguments")
    if isinstance(raw, dict):
        return raw
    if not isinstance(raw, str) or not raw.strip():
        return None
    try:
        decoded = json.loads(raw)
    except ValueError:
        return None
    return decoded if isinstance(decoded, dict) else None


def _command_text(arguments):
    if not arguments:
        return None
    for key in ("command", "cmd", "script", "shell_command", "commandLine"):
        value = arguments.get(key)
        if isinstance(value, str) and value.strip():
            return value
    return None


def _target_paths(arguments):
    if not arguments:
        return []
    paths = []
    for key in ("path", "file_path", "filePath", "file", "target", "destination", "dest", "source"):
        value = arguments.get(key)
        if isinstance(value, str) and value:
            paths.append(value)
    return paths


def _under_scratch(path, scratch_dir):
    normalized = os.path.normpath(path)
    scratch = os.path.normpath(scratch_dir)
    return normalized == scratch or normalized.startswith(scratch + os.sep)


def shell_is_read_only(command):
    """True only when EVERY segment of `command` is known inspection.

    An unparseable command is not read-only. That mirrors the policy gate's class 28: a command the
    host cannot analyse is never given the benefit of the doubt.
    """
    parsed = shellparse.parse(command)
    if not parsed.ok or not parsed.segments:
        return False
    for segment in parsed.segments:
        program = (segment.program or "").rsplit("/", 1)[-1]
        if program == "git":
            argv = [word for word in segment.argv[1:] if not word.startswith("-")]
            if not argv or argv[0] not in READ_ONLY_GIT:
                return False
            continue
        if program not in READ_ONLY_PROGRAMS:
            return False
    return True


class ToolCallRecord:
    """One attempted or dispatched tool call, with what the host could tell about it."""

    def __init__(self, order, event, dispatched, scratch_dir):
        call = event.get("tool_call") or {}
        function = call.get("function") if isinstance(call.get("function"), dict) else {}
        self.order = order
        self.event_type = event.get("type")
        self.dispatched = dispatched
        self.id = call.get("id")
        self.name = function.get("name") or ""
        self.agent = event.get("agent_name")
        self.timestamp = event.get("timestamp")
        self.arguments = _arguments(call)
        self.command = _command_text(self.arguments)
        self.paths = _target_paths(self.arguments)
        definition = event.get("tool_definition")
        hints = definition.get("annotations") if isinstance(definition, dict) else None
        hint = hints.get("readOnlyHint") if isinstance(hints, dict) else None
        self.read_only_hint = hint if isinstance(hint, bool) else None
        self.response = None
        self.scratch_only = bool(self.paths) and all(
            _under_scratch(path, scratch_dir) for path in self.paths)
        self.mutation = self._is_mutation()

    def _is_mutation(self):
        lowered = self.name.lower()
        if lowered in READ_ONLY_TOOLS:
            return False
        if self.read_only_hint is True:
            return False
        if lowered in SHELL_TOOLS:
            # A build or test command IS a mutation (data-model.md): it can rewrite the workspace.
            # Only commands the host can prove are inspection are exempt.
            if self.command is None:
                return True
            return not shell_is_read_only(self.command)
        if self.scratch_only:
            # A write confined to /run/dca/out is run evidence, not workspace state.
            return False
        return True

    def writes(self, filename, scratch_dir=SCRATCH_DIR):
        target = os.path.join(scratch_dir, filename)
        return any(os.path.normpath(path) == os.path.normpath(target) for path in self.paths)

    def as_dict(self):
        return {"order": self.order, "id": self.id, "name": self.name, "agent": self.agent,
                "timestamp": self.timestamp, "dispatched": self.dispatched,
                "mutation": self.mutation, "scratch_only": self.scratch_only,
                "command": self.command}


# --- native ceilings --------------------------------------------------------------------------


def _native_ceiling_detail(event):
    """The `limits.native_ceiling` object for a ceiling event, or None if this is not one.

    Every value is coerced to a string because the report schema types `budget`, `limit`, `used`
    and `max` as strings, and the runtime emits some of them as numbers.
    """
    kind = event.get("type")
    if kind == "budget_exceeded":
        detail = {"event": "budget_exceeded"}
        for key in ("budget", "limit", "used", "max"):
            if event.get(key) is not None:
                detail[key] = str(event[key])
        config_path = event.get("config_path")
        if not isinstance(config_path, str) or not config_path:
            budget = event.get("budget")
            config_path = f"budget.{budget}" if budget else "budget"
        detail["config_path"] = config_path
        return detail
    if kind == "max_iterations_reached":
        agent = event.get("agent_name") or "root"
        detail = {"event": "max_iterations_reached",
                  "config_path": event.get("config_path")
                  or f"agents.{agent}.max_iterations"}
        value = event.get("max_iterations")
        if isinstance(value, bool):
            value = None
        if isinstance(value, int):
            detail["max_iterations"] = value
        elif isinstance(value, str) and value.isdigit():
            detail["max_iterations"] = int(value)
        return detail
    if kind == "error":
        code = event.get("code")
        if not isinstance(code, str):
            nested = event.get("error")
            code = nested.get("code") if isinstance(nested, dict) else None
        if code != "loop_detected":
            return None
        agent = event.get("agent_name") or "root"
        return {"event": "loop_detected",
                "config_path": event.get("config_path")
                or f"agents.{agent}.max_consecutive_tool_calls"}
    return None


# --- verification and retries -------------------------------------------------------------------

#: Substrings that mean a command reported failure. Anything else with output is taken as a pass.
#: Getting this wrong in the "fail" direction only over-counts retries, which stops the run sooner;
#: getting it wrong in the "pass" direction would let a repair loop run unbounded, so the bias is
#: deliberate.
FAILURE_MARKERS = (
    "exit status", "exit code", "command failed", "non-zero exit",
    "traceback (most recent call last)", "failed\n", "failures=", "error:", "fatal:",
)


def response_outcome(text):
    """`pass` / `fail` / `unknown` for one check execution, from its tool response only."""
    if text is None:
        return "unknown"
    lowered = text.lower()
    for marker in FAILURE_MARKERS:
        if marker in lowered:
            return "fail"
    return "pass"


def _normalize_command(text):
    return " ".join(text.split())


def _matches_check(record, commands):
    """Which declared verification command, if any, this tool call executed."""
    haystack = _normalize_command(record.command) if record.command else None
    if haystack is None:
        return None
    for command in commands:
        needle = _normalize_command(command)
        if needle and needle in haystack:
            return needle
    return None


# --- the analysis -----------------------------------------------------------------------------


class RunAnalysis:
    """Everything the host concluded about one Docker Agent run. Fail-closed by construction."""

    def __init__(self):
        self.stream = MALFORMED
        self.agent_exit = ABNORMAL
        self.sandbox_created = True
        self.events = 0
        self.event_types = {}
        self.unknown_types = []
        self.problems = []
        self.tool_calls = []
        self.steps = 0
        self.distinct_tool_call_ids = 0
        self.retries = 0
        self.verification_runs = 0
        self.tokens = 0
        self.token_usage = None
        self.limit_reached = None
        self.native_ceiling = None
        self.host_stop_reason = None
        self.first_mutation = None
        self.context_record_at = None
        self.plan_at = None
        self.stop_reason = None

    @property
    def ok(self):
        """A stream the host fully understood. `host-terminated` is understood, not broken."""
        return self.stream in (COMPLETE, HOST_TERMINATED)

    @property
    def context_precedes_first_mutation(self):
        """FR-001/FR-008 ordering. No mutation at all trivially satisfies it."""
        if self.first_mutation is None:
            return True
        if self.context_record_at is None:
            return False
        return self.context_record_at < self.first_mutation

    def counters(self):
        return {"steps": self.steps, "retries": self.retries,
                "verification_runs": self.verification_runs, "tokens": self.tokens}

    def run_integrity(self):
        return {"stream": self.stream, "agent_exit": self.agent_exit,
                "sandbox_created": self.sandbox_created}

    def as_dict(self):
        return {
            "run_integrity": self.run_integrity(),
            "counters": self.counters(),
            "events": self.events,
            "event_types": dict(sorted(self.event_types.items())),
            "unknown_types": sorted(set(self.unknown_types)),
            "problems": list(self.problems),
            "limit_reached": self.limit_reached,
            "native_ceiling": self.native_ceiling,
            "first_mutation": self.first_mutation,
            "context_record_at": self.context_record_at,
            "plan_ref_written": self.plan_at is not None,
            "token_usage": self.token_usage,
        }


def _valid_tool_call(event):
    """Is this `tool_call` structurally sound enough to COUNT and to identify?

    Only what the count depends on is required. `function.arguments` is present on some real
    `tool_call` events and absent on others, so requiring it would reject genuine traffic - and a
    parser that rejects real streams fails just as badly as one that counts imaginary ones.
    """
    call = event.get("tool_call")
    if not isinstance(call, dict):
        return "tool_call event has no tool_call object"
    if not isinstance(call.get("id"), str) or not call["id"]:
        return "tool_call event has no usable tool_call.id"
    function = call.get("function")
    if not isinstance(function, dict):
        return "tool_call event has no function object"
    if not isinstance(function.get("name"), str) or not function["name"]:
        return "tool_call event has no function.name"
    return None


def _split_lines(text):
    """(complete lines, trailing fragment). A capture that ends mid-line was cut."""
    lines = text.split("\n")
    fragment = ""
    if lines and lines[-1] != "":
        fragment = lines[-1]
        lines = lines[:-1]
    return [line for line in lines if line.strip()], fragment


def analyze(text, exit_status=None, host_stop=None, sandbox_created=True,
            verification_commands=(), scratch_dir=SCRATCH_DIR):
    """Read one captured outer stream and say what the run was.

    `host_stop` is the launcher's own record that IT stopped the run: `{"reason": one of
    HOST_LIMIT_REASONS}`. It is passed in rather than inferred, because `host-terminated` is a
    statement about what the host did and the stream cannot testify to that.

    `sandbox_created=False` means the run was blocked before provisioning; there is no stream and
    no agent, and the report schema requires exactly the shape produced here.
    """
    result = RunAnalysis()
    result.sandbox_created = bool(sandbox_created)
    if not result.sandbox_created:
        result.stream = NONE
        result.agent_exit = NOT_STARTED
        result.stop_reason = "no sandbox was created"
        return result

    lines, fragment = _split_lines(text or "")
    structural = []
    saw_terminal = False
    typed = []

    for number, line in enumerate(lines, 1):
        try:
            event = json.loads(line)
        except ValueError:
            structural.append(f"line {number} is not valid JSON")
            continue
        if not isinstance(event, dict):
            structural.append(f"line {number} is not a JSON object")
            continue
        kind = event.get("type")
        if not isinstance(kind, str) or not kind:
            structural.append(f"line {number} has no string type")
            continue
        if kind not in RECOGNIZED_EVENTS:
            result.unknown_types.append(kind)
            structural.append(f"line {number} is an unrecognized event type {kind!r}")
            continue

        result.events += 1
        result.event_types[kind] = result.event_types.get(kind, 0) + 1
        typed.append(event)

        if kind in ATTEMPT_EVENTS:
            dispatched = kind == TOOL_CALL_EVENT
            if dispatched:
                problem = _valid_tool_call(event)
                if problem:
                    # A damaged tool_call is a stream-level finding and is NOT counted: a broken
                    # event must never inflate the number a host limit is computed from.
                    structural.append(f"line {number}: {problem}")
                    continue
            call = event.get("tool_call")
            if not isinstance(call, dict):
                continue
            record = ToolCallRecord(len(result.tool_calls), event, dispatched, scratch_dir)
            result.tool_calls.append(record)
            if dispatched:
                result.steps += 1
        elif kind == "tool_call_response":
            identifier = event.get("tool_call_id")
            response = event.get("response")
            if isinstance(identifier, str):
                for record in reversed(result.tool_calls):
                    if record.id == identifier:
                        record.response = response if isinstance(response, str) else None
                        break
        elif kind == TERMINAL_EVENT:
            if not isinstance(event.get("reason"), str):
                structural.append(f"line {number}: stream_stopped has no reason")
                continue
            saw_terminal = True
            result.stop_reason = event.get("reason")

    result.problems = structural
    result.distinct_tool_call_ids = len({r.id for r in result.tool_calls if r.dispatched})

    _account_tokens(result, typed)
    _account_ordering(result, scratch_dir)
    _account_retries(result, verification_commands)

    ceiling = _terminal_native_ceiling(typed)
    _classify(result, structural, fragment, saw_terminal, exit_status, host_stop, ceiling)
    return result


def _terminal_native_ceiling(typed):
    """The ceiling detail when a ceiling event is the LAST typed event of the run, else None.

    "Last" ignores a trailing `stream_stopped`, which is the runtime closing the stream, and
    ignores nothing else: a ceiling followed by real activity - a sub-agent stopped while root
    continued - did not end the run, so it is logged and classification moves on.
    """
    for event in reversed(typed):
        if event.get("type") == TERMINAL_EVENT:
            continue
        return _native_ceiling_detail(event)
    return None


def _account_tokens(result, typed):
    """Total tokens across every agent session, from `token_usage`.

    `usage.input_tokens` / `usage.output_tokens` are CUMULATIVE per session, and root, researcher
    and reviewer each have their own session. So the total is the sum over sessions of each
    session's high-water mark - summing the events themselves would multiply the same tokens by
    how often the runtime reported them.
    """
    sessions = {}
    saw_cost = False
    for event in typed:
        if event.get("type") != "token_usage":
            continue
        usage = event.get("usage")
        if not isinstance(usage, dict):
            continue
        key = (event.get("agent_name"), event.get("session_id"))
        current = sessions.setdefault(key, {"input": 0, "output": 0})
        for field, name in (("input_tokens", "input"), ("output_tokens", "output")):
            value = usage.get(field)
            if isinstance(value, int) and not isinstance(value, bool):
                current[name] = max(current[name], value)
        if isinstance(usage.get("cost"), (int, float)) and not isinstance(usage["cost"], bool):
            saw_cost = True
    if not sessions:
        result.tokens = 0
        result.token_usage = None
        return
    total_in = sum(item["input"] for item in sessions.values())
    total_out = sum(item["output"] for item in sessions.values())
    result.tokens = total_in + total_out
    result.token_usage = {"input_tokens": total_in, "output_tokens": total_out,
                          "total_tokens": total_in + total_out,
                          "sessions": len(sessions)}
    if saw_cost:
        # Cost is reported per session as a running total; sum the high-water marks the same way.
        result.token_usage["cost"] = round(sum(
            _session_cost(typed, key) for key in sessions), 6)


def _session_cost(typed, key):
    best = 0.0
    for event in typed:
        if event.get("type") != "token_usage":
            continue
        if (event.get("agent_name"), event.get("session_id")) != key:
            continue
        usage = event.get("usage")
        value = usage.get("cost") if isinstance(usage, dict) else None
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            best = max(best, float(value))
    return best


def _account_ordering(result, scratch_dir):
    """First workspace mutation, and when the Context Record and Plan were written (FR-001)."""
    for record in result.tool_calls:
        if result.context_record_at is None and record.writes(CONTEXT_RECORD, scratch_dir):
            result.context_record_at = record.order
        if result.plan_at is None and record.writes(PLAN_FILE, scratch_dir):
            result.plan_at = record.order
        if result.first_mutation is None and record.mutation:
            result.first_mutation = record.order


def _account_retries(result, verification_commands):
    """Retries = repair/re-verify cycles (data-model.md "counters").

    One retry is a workspace change followed by re-execution of a required check that had not
    passed. A re-run with no intervening mutation is not a repair cycle - it is the same check
    asked twice - and a re-run of a check that already passed is not a repair either.
    """
    commands = [c for c in (verification_commands or []) if isinstance(c, str) and c.strip()]
    if not commands:
        return
    last_run = {}       # command -> (order, outcome)
    mutated_since = {}  # command -> bool
    for record in result.tool_calls:
        if not record.dispatched:
            continue
        matched = _matches_check(record, commands)
        if matched is None:
            # Only a change that is NOT itself a declared check can be the "repair" half of a
            # cycle. Running the check counts as a workspace mutation elsewhere (a build can
            # rewrite the tree), but treating it as the repair would make every second run of a
            # failing check look like a repair that never happened.
            if record.mutation:
                for command in list(mutated_since):
                    mutated_since[command] = True
            continue
        result.verification_runs += 1
        previous = last_run.get(matched)
        if previous is not None and mutated_since.get(matched) and previous[1] != "pass":
            result.retries += 1
        last_run[matched] = (record.order, response_outcome(record.response))
        mutated_since[matched] = False


def _classify(result, structural, fragment, saw_terminal, exit_status, host_stop, ceiling):
    """Set `stream`, `agent_exit` and `limit_reached`. Order is the whole point.

    Corruption outranks everything: a stream damaged in the middle is not merely unfinished, and no
    later fact can make it readable. After that a host stop outranks a native ceiling, because the
    host is the authority on direct/planned limits, and a native ceiling outranks a bad exit
    status, because the runtime stopping itself on purpose is a task outcome, not a crash.
    """
    if structural:
        result.stream = MALFORMED
        result.agent_exit = ABNORMAL if not host_stop else HOST_LIMIT
        if host_stop:
            result.limit_reached = _host_reason(host_stop)
            result.host_stop_reason = result.limit_reached
        return

    if host_stop:
        reason = _host_reason(host_stop)
        result.stream = HOST_TERMINATED
        result.agent_exit = HOST_LIMIT
        result.limit_reached = reason
        result.host_stop_reason = reason
        result.stop_reason = f"host limit: {reason}"
        return

    if ceiling is not None:
        # Deterministic, whatever the exit status: the runtime stopped the run on purpose.
        result.stream = COMPLETE
        result.agent_exit = NORMAL
        result.limit_reached = NATIVE_CEILING
        result.native_ceiling = ceiling
        result.stop_reason = f"native ceiling: {ceiling['config_path']}"
        return

    if fragment:
        result.stream = MALFORMED if saw_terminal else TRUNCATED
        result.problems = ["the capture ends mid-line"
                           + (" after the terminal event" if saw_terminal else "")]
        result.agent_exit = ABNORMAL
        return

    if not saw_terminal:
        result.stream = TRUNCATED
        result.problems = [f"the stream has no terminal {TERMINAL_EVENT} event"]
        result.agent_exit = ABNORMAL
        return

    result.stream = COMPLETE
    result.agent_exit = _exit_classification(exit_status)


def _host_reason(host_stop):
    reason = host_stop.get("reason") if isinstance(host_stop, dict) else host_stop
    if reason not in HOST_LIMIT_REASONS:
        raise ValueError(f"not a host limit reason: {reason!r}")
    return reason


def _exit_classification(exit_status):
    if exit_status is None:
        return ABNORMAL
    try:
        status = int(exit_status)
    except (TypeError, ValueError):
        return ABNORMAL
    return NORMAL if status == 0 else ABNORMAL


def analyze_file(path, **kwargs):
    with open(path, encoding="utf-8", errors="replace") as handle:
        return analyze(handle.read(), **kwargs)


# --- incremental accounting for host-side limit enforcement -------------------------------------


class StreamAccountant:
    """Counters maintained line by line while the agent is still running (tasks.md T069).

    `analyze()` is the authority for the REPORT: it classifies the whole stream fail-closed once
    the process has ended. This class exists for the other job - deciding, mid-run, that a host
    limit has been reached and the run must be stopped now. It therefore reads each line as it
    arrives and keeps only what a limit is computed from.

    It deliberately shares `ToolCallRecord`, `_account_retries` and the token rule with `analyze()`
    rather than re-deriving them. Two independent implementations of "how many steps have there
    been" would eventually disagree, and the disagreement would show up as a run stopped at the
    wrong moment or not stopped at all.

    A line it cannot read is NOT a counting error: a malformed stream is a classification the final
    `analyze()` makes with the whole picture. Here it only means this line adds nothing.
    """

    def __init__(self, verification_commands=(), scratch_dir=SCRATCH_DIR):
        self.verification_commands = [c for c in (verification_commands or [])
                                      if isinstance(c, str) and c.strip()]
        self.scratch_dir = scratch_dir
        self.tool_calls = []
        self.steps = 0
        self.retries = 0
        self.verification_runs = 0
        self.tokens = 0
        self.first_mutation = None
        self.context_record_at = None
        self._sessions = {}

    def feed(self, line):
        """Read one raw stream line. Returns True when it changed a counter."""
        line = line.strip()
        if not line:
            return False
        try:
            event = json.loads(line)
        except ValueError:
            return False
        if not isinstance(event, dict):
            return False
        kind = event.get("type")
        if kind == TOOL_CALL_EVENT and not _valid_tool_call(event):
            record = ToolCallRecord(len(self.tool_calls), event, True, self.scratch_dir)
            self.tool_calls.append(record)
            self.steps += 1
            if self.context_record_at is None and record.writes(CONTEXT_RECORD, self.scratch_dir):
                self.context_record_at = record.order
            if self.first_mutation is None and record.mutation:
                self.first_mutation = record.order
            self._recount_retries()
            return True
        if kind in ATTEMPT_EVENTS and kind != TOOL_CALL_EVENT:
            call = event.get("tool_call")
            if isinstance(call, dict):
                record = ToolCallRecord(len(self.tool_calls), event, False, self.scratch_dir)
                self.tool_calls.append(record)
                if self.first_mutation is None and record.mutation:
                    self.first_mutation = record.order
            return True
        if kind == "tool_call_response":
            identifier = event.get("tool_call_id")
            response = event.get("response")
            for record in reversed(self.tool_calls):
                if record.id == identifier:
                    record.response = response if isinstance(response, str) else None
                    break
            self._recount_retries()
            return True
        if kind == "token_usage":
            usage = event.get("usage")
            if not isinstance(usage, dict):
                return False
            key = (event.get("agent_name"), event.get("session_id"))
            current = self._sessions.setdefault(key, {"input": 0, "output": 0})
            for field, name in (("input_tokens", "input"), ("output_tokens", "output")):
                value = usage.get(field)
                if isinstance(value, int) and not isinstance(value, bool):
                    current[name] = max(current[name], value)
            self.tokens = sum(item["input"] + item["output"] for item in self._sessions.values())
            return True
        return False

    def _recount_retries(self):
        if not self.verification_commands:
            return
        snapshot = RunAnalysis()
        snapshot.tool_calls = self.tool_calls
        _account_retries(snapshot, self.verification_commands)
        self.retries = snapshot.retries
        self.verification_runs = snapshot.verification_runs

    def counters(self):
        return {"steps": self.steps, "retries": self.retries,
                "verification_runs": self.verification_runs, "tokens": self.tokens}

    def limit_reached(self, configured):
        """The FIRST host limit this run has reached, or None. Order is fixed for determinism.

        `steps` is checked before `retries` and `tokens` so that two runs with the same stream
        always record the same reason; a "whichever we noticed first" rule would make the recorded
        limit depend on line arrival timing.
        """
        for reason, value in (("steps", self.steps), ("retries", self.retries),
                              ("tokens", self.tokens)):
            ceiling = (configured or {}).get(reason)
            if isinstance(ceiling, int) and ceiling > 0 and value >= ceiling:
                return reason
        return None
