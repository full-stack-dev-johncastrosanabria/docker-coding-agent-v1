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
    event = (payload.get("hook_event_name") or payload.get("event")
             or (payload.get("hook_specific_output") or {}).get("hook_event_name") or "unknown")
    agent = (payload.get("agent_name") or payload.get("agent_type")
             or payload.get("subagent_type") or payload.get("agent") or "unknown")
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
