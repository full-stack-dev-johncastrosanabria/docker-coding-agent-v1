"""Background cleanup."""

from legacy import stamp


def sweep(count):
    """Report a completed sweep."""
    return f"swept {count} at {stamp()}"
