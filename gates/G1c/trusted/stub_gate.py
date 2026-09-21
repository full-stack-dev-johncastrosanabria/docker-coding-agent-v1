"""G1c PreToolUse stub gate (tasks.md T017), installed under the trusted root /opt/dca/lib/dca.

It is invoked through a wrapper that runs it with a cleared environment and an isolated
interpreter, so neither the repository's env block nor its PYTHONPATH can reach it.

Locating the sibling module is explicit, and has to be. `python3 -I` implies `-P`, so the
interpreter does NOT put the script's own directory on sys.path - an isolated interpreter starts
with no script-relative entry at all. Relying on the implicit entry makes every import fail, which
denies every call including the allowed ones. The trusted root is therefore derived from this
script's own absolute path, which is the path the wrapper names, and inserted deliberately. That
is strictly narrower than the implicit behaviour: the directory can never come from the working
directory or from PYTHONPATH, both of which the wrapper has already neutralized, so the
same-named module a hostile repository ships in its working directory cannot win.

Exit codes are the Claude Code hook contract: 0 lets the call proceed, 2 blocks it. Every other
outcome is mapped to 2 by the wrapper, so a crash, a missing interpreter or a missing module deny
rather than fall through.
"""

import json
import os
import sys

LOG = "/tmp/dca-g1c-gate.log"

# The directory this script really lives in - the trusted root - and nothing else.
TRUSTED_ROOT = os.path.dirname(os.path.abspath(__file__))


def main():
    sys.path.insert(0, TRUSTED_ROOT)
    try:
        import dca_gate_support as support
    except Exception:                     # noqa: BLE001 - any import failure denies
        return 2

    raw = sys.stdin.read()
    try:
        event = json.loads(raw)
    except ValueError:
        event = None

    # The evidence the gate itself produces: which module actually loaded, from where, and what
    # this invocation decided. record() is never trusted to be honest about the decision - the
    # decision is taken here.
    blocked = True if event is None else bool(support.is_marker_action(event))
    try:
        with open(LOG, "a", encoding="utf-8") as fh:
            fh.write(json.dumps({
                "support_origin": getattr(support, "ORIGIN", None),
                "support_file": getattr(support, "__file__", None),
                "tool_name": (event or {}).get("tool_name"),
                "command": ((event or {}).get("tool_input") or {}).get("command"),
                "decision": "block" if blocked else "allow",
                "cwd": os.getcwd(),
                "sys_path0": sys.path[0] if sys.path else None,
            }) + "\n")
    except OSError:
        return 2

    return 2 if blocked else 0


if __name__ == "__main__":
    sys.exit(main())
