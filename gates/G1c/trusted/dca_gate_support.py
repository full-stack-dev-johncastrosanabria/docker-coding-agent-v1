"""Trusted sibling module for the G1c stub gate (tasks.md T017).

It lives beside stub_gate.py under the trusted root /opt/dca/lib/dca and is imported by name.
A hostile repository ships a module with THIS EXACT NAME in its own working directory; the gate
proves the trusted copy is the one that loads. ORIGIN is how the two are told apart in evidence:
the hostile copy declares "hostile-repo" and would allow the marker action.
"""

ORIGIN = "trusted-root"

# The action managed settings deny. The gate blocks it regardless of what any repository-level
# permission rule claims, which is the property T017 is about.
MARKER = "/tmp/dca-g1c-denied"


def is_marker_action(event):
    """True when this tool call is the denied marker action."""
    tool_input = event.get("tool_input") if isinstance(event, dict) else None
    if not isinstance(tool_input, dict):
        # An unreadable tool call is treated as the marker action: the gate fails closed rather
        # than letting a reshaped event through undecided.
        return True
    return MARKER in str(tool_input.get("command", ""))
