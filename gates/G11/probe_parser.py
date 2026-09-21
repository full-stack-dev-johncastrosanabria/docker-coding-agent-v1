"""Prototype outer-event parser for gate G11 part A (tasks.md T020).

Standard library only. This is the SPIKE that T040 builds `src/dca/events.py` from; it is
deliberately gate-local and disposable. It is not a general event framework, it supports exactly
one runtime - the pinned docker_agent v1.136.0 - and it refuses everything else.

THE PROPERTY IT EXISTS TO PROVE. Host-side processing must be able to rely on Docker Agent's typed
OUTER event stream: one JSON object per line, each a recognized typed event. A tool call is counted
because the runtime emitted a typed `tool_call` event, never because some text looked like one. That
distinction is the whole gate: tool output, shell output and model prose all travel INSIDE event
fields, so a line of JSON printed by a tool is payload and can never become an outer event.

WHY `tool_call` IS THE COUNTABLE EVENT, AND THE OTHERS ARE NOT. Across every real capture this
repository has taken from the pinned runtime (G1b, G1c, G1d for Claude; G3 for Codex):

  * `partial_tool_call` is the streaming delta. It carries the SAME `tool_call.id` as the final
    event - 17 partials for 2 calls in one G1c capture - so counting it multiplies a single call;
  * `tool_call_response` is the RESULT, and it is emitted even when no call was dispatched: G3's
    approval cases recorded 0 `tool_call` and 1 `tool_call_response` each, because a hook blocked
    or declined the call. Counting responses counts attempts, not calls;
  * `hook_blocked` and `tool_call_confirmation` carry a `tool_call` object and a `tool_definition`
    with THE SAME KEY SET as a real `tool_call` event.

That last point is the trap. Four distinct event types are structurally identical, so a parser that
recognized a tool call by its shape would over-count by every blocked and unconfirmed call. Only the
`type` string separates them, which is precisely why this parser dispatches on `type` first and uses
shape only to validate the event it has already identified.

FAIL-CLOSED CLASSIFICATION. A stream is `complete` only if every line is a recognized, structurally
valid typed event AND the terminal `stream_stopped` event is present. Anything else is `malformed`
or `truncated`, and neither is ever a successful run. An UNRECOGNIZED type is malformed rather than
ignored: the runtime is pinned, so a type this parser has never observed means host processing does
not understand the stream it is reading, and claiming success there is exactly the assumption T020
exists to test.
"""

import json

# The outer event types the PINNED docker_agent v1.136.0 was actually OBSERVED emitting, across
# every `docker agent run --exec --json` capture this repository holds. The set is empirical, not
# guessed from documentation and not inferred from binary strings: the binary's Go string table is
# grouped by length and interleaves unrelated constants, so it cannot yield a trustworthy set.
# A type outside this set fails closed rather than being tolerated.
RECOGNIZED_EVENTS = frozenset({
    "agent_choice",
    # Reasoning deltas on the native chatgpt path. It is in this set because the pinned runtime was
    # OBSERVED emitting it during this gate's own Codex runs, not because documentation mentions it:
    # no earlier capture in this repository contains it, and the first G11 run classified those
    # streams `malformed` for exactly that reason. That is the fail-closed rule working as intended
    # - an unrecognized type stopped the gate instead of being quietly ignored. Its shape is
    # identical to agent_choice (agent_name, content, session_id, timestamp, type) and it is not a
    # tool call, so recognizing it changes nothing about what is countable.
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

# The ONE event type that represents a dispatched tool call. See the module docstring for why the
# three structurally identical siblings are not countable.
TOOL_CALL_EVENT = "tool_call"

# The terminal event of a complete stream. Every complete capture ends with it; the one capture that
# does not is G3's rejected gpt-5.6 probe, where the backend refused the model and the stream simply
# stopped - the real-world truncation this classification is meant to catch.
TERMINAL_EVENT = "stream_stopped"

COMPLETE = "complete"
MALFORMED = "malformed"
TRUNCATED = "truncated"


class ParseResult:
    """What one capture is, stated so a caller cannot read a failure as a success by accident."""

    def __init__(self):
        self.classification = MALFORMED      # fail-closed default; nothing is valid until proven
        self.events = 0
        self.tool_calls = 0
        self.tool_call_ids = []
        self.event_types = {}
        self.unknown_types = []
        self.problems = []

    @property
    def ok(self):
        return self.classification == COMPLETE

    def as_dict(self):
        return {
            "classification": self.classification,
            "events": self.events,
            "tool_calls": self.tool_calls,
            "distinct_tool_call_ids": len(set(self.tool_call_ids)),
            "event_types": dict(sorted(self.event_types.items())),
            "unknown_types": sorted(set(self.unknown_types)),
            "problems": self.problems,
        }


def _valid_tool_call(event):
    """Is this `tool_call` event structurally sound enough to COUNT and to identify?

    Only the fields the count actually relies on are required. Validating more would reject
    legitimate variation the pinned runtime really emits - `function.arguments` is present on some
    `tool_call` events and absent on others - and a parser that rejects real traffic fails the gate
    just as surely as one that counts imaginary traffic.
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


def parse(text):
    """Classify one captured outer stream and count its tool calls.

    `text` is the raw bytes of a `docker agent run --exec --json` capture, decoded. Nothing is
    repaired, nothing is skipped: a line that is not a recognized typed event is a finding about the
    whole stream, not a line to step over.
    """
    result = ParseResult()
    lines = text.split("\n")
    # A capture that ends with a newline yields a final empty element that is not a fragment.
    fragment = ""
    if lines and lines[-1] != "":
        fragment = lines[-1]
    lines = [l for l in lines[:len(lines) - 1] if l.strip()] if fragment else \
        [l for l in lines if l.strip()]

    saw_terminal = False
    structural = []
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
            # Never silently tolerated: an unrecognized type on a PINNED runtime means host
            # processing does not understand what it is reading.
            result.unknown_types.append(kind)
            structural.append(f"line {number} is an unrecognized event type {kind!r}")
            continue

        result.events += 1
        result.event_types[kind] = result.event_types.get(kind, 0) + 1

        if kind == TOOL_CALL_EVENT:
            problem = _valid_tool_call(event)
            if problem:
                # A malformed tool_call is a stream-level finding and is NOT counted. Counting it
                # would let a damaged event inflate the number host limits are computed from.
                structural.append(f"line {number}: {problem}")
                continue
            result.tool_calls += 1
            result.tool_call_ids.append(event["tool_call"]["id"])
        elif kind == TERMINAL_EVENT:
            if not isinstance(event.get("reason"), str):
                structural.append(f"line {number}: stream_stopped has no reason")
                continue
            saw_terminal = True

    result.problems = structural

    # Classification order is deliberate and fail-closed. A corrupt line anywhere outranks a missing
    # tail, because a stream that was damaged in the middle is not merely unfinished.
    if structural:
        result.classification = MALFORMED
    elif fragment:
        # The capture stops mid-line. That is a truncation only if the stream had not already
        # finished; a complete stream with trailing garbage is corruption, not a cut.
        result.classification = MALFORMED if saw_terminal else TRUNCATED
        result.problems = [
            "the capture ends mid-line" + ("" if not saw_terminal else " after the terminal event")]
    elif not saw_terminal:
        result.classification = TRUNCATED
        result.problems = [f"the stream has no terminal {TERMINAL_EVENT} event"]
    else:
        result.classification = COMPLETE
    return result


def parse_file(path):
    with open(path, encoding="utf-8", errors="replace") as fh:
        return parse(fh.read())


# --- run outcome --------------------------------------------------------------------------------

SUCCESS = "success"
ABNORMAL = "abnormal"
INCOMPLETE = "incomplete"


def run_outcome(result, exit_status):
    """(outcome, why) for a whole Docker Agent run: its stream AND how the process ended.

    A complete stream is necessary but not sufficient. T020 criterion 4 is that abruptly killing
    Docker Agent can never read as a successful run, so a non-zero or signalled exit is decisive on
    its own - the parser never gets to overrule it with a stream that happens to look finished.
    """
    if exit_status is None:
        return ABNORMAL, "the agent exit status was not observed"
    try:
        status = int(exit_status)
    except (TypeError, ValueError):
        return ABNORMAL, f"the agent exit status {exit_status!r} is not a number"
    if status != 0:
        # POSIX shells report a signalled child as 128+signum; either way it is not a normal exit.
        signal = status - 128 if status > 128 else None
        return ABNORMAL, (
            f"the agent exited {status}"
            + (f" (killed by signal {signal})" if signal else "")
            + f", and its stream was {result.classification}")
    if not result.ok:
        return INCOMPLETE, f"the agent exited 0 but its stream was {result.classification}"
    return SUCCESS, "the agent exited 0 and its stream is complete"
