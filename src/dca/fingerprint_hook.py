"""Subagent fingerprint hook (tasks.md T053, T055; FR-022).

Standard library only, run inside the VM by `/opt/dca/bin/dca-fingerprint` from Claude's managed
`SubagentStart`/`SubagentStop` hooks and Codex's `on_agent_switch`/`subagent_stop` hooks.

It records the candidate's workspace fingerprint on both sides of a delegation, so the launcher can
tell whether the candidate changed while the read-only reviewer was reading it - which FR-022 makes
a safety-invariant violation, not a detail.

IT NEVER BLOCKS. The exit status is always 0. A fingerprint that could not be taken is evidence
that is simply absent, and absent review evidence already prevents a planned task from being
reported as a success. Making this hook able to fail a tool call would add a new way for the run to
die without adding any protection the missing evidence does not already provide.

THE RECORD IS ADVISORY IN-VM STATE. Like every other file under `/run/dca/state/`, a process with
sudo could rewrite it. The host reads it as a claim and refuses to treat `identical` as true unless
it has both sides; the enforcement boundary stays host-side.
"""

import datetime
import json
import os
import sys

_SELF = os.path.realpath(__file__)
_TRUSTED_ROOT = os.path.dirname(os.path.dirname(_SELF))          # <prefix>/opt/dca/lib
_PREFIX = os.path.dirname(os.path.dirname(os.path.dirname(_TRUSTED_ROOT))) or "/"
RUN_DIR = os.path.join(_PREFIX, "run", "dca")
STATE_DIR = os.path.join(RUN_DIR, "state")
RECORD = os.path.join(STATE_DIR, "fingerprints.jsonl")

sys.path[:] = [_TRUSTED_ROOT] + [p for p in sys.path
                                 if p and os.path.isdir(p) and "site-packages" not in p
                                 and not p.startswith(os.getcwd())]

try:
    from dca import fingerprint as fingerprint_module
except Exception:  # pragma: no cover - an unusable trusted root is recorded, never fatal
    fingerprint_module = None


def _payload():
    try:
        raw = sys.stdin.read()
    except Exception:
        return {}
    if not raw.strip():
        return {}
    try:
        decoded = json.loads(raw)
    except ValueError:
        return {}
    return decoded if isinstance(decoded, dict) else {}


def _event_and_agent(payload):
    """The event name and the agent the record belongs to.

    `to_agent` is in the chain because of a real gap: a switch event names the agent being switched
    TO, not an `agent_name`. The vendor schema is explicit - `subagent_stop` "the sub-agent's name is
    in agent_name", while `on_agent_switch` "receives from_agent, to_agent, and agent_switch_kind".
    Without `to_agent` every switch record resolved to "unknown", which left that backend with a
    reviewer STOP and no reviewer START, so the host could never pair the two and every planned run
    was unprovable. It comes last so a payload that does carry an explicit name still wins, and
    `from_agent` is deliberately NOT read: it names the agent being left, so on the switch into the
    reviewer it would attribute the record to whoever delegated.
    """
    event = (payload.get("hook_event_name") or payload.get("event")
             or (payload.get("hook_specific_output") or {}).get("hook_event_name") or "unknown")
    agent = (payload.get("agent_name") or payload.get("agent_type")
             or payload.get("subagent_type") or payload.get("agent")
             or payload.get("to_agent") or "unknown")
    return str(event), str(agent)


def main(argv=None):
    payload = _payload()
    event, agent = _event_and_agent(payload)
    record = {
        "ts": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "event": event,
        "agent": agent,
        "fingerprint": None,
        "error": None,
    }
    try:
        with open(os.path.join(RUN_DIR, "run.json"), encoding="utf-8") as handle:
            run = json.load(handle)
        workspace = run["workspace"]
        if fingerprint_module is None:
            raise RuntimeError("the fingerprint module is not importable from the trusted root")
        record["workspace"] = workspace
        record["fingerprint"] = fingerprint_module.workspace_fingerprint(workspace)
    except Exception as exc:
        record["error"] = f"{type(exc).__name__}: {exc}"

    try:
        os.makedirs(STATE_DIR, exist_ok=True)
        with open(RECORD, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, sort_keys=True) + "\n")
    except Exception:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
