"""HTTP handlers."""

from legacy.timeutil import stamp


def health():
    """The health payload."""
    return {"status": "ok", "at": stamp()}
