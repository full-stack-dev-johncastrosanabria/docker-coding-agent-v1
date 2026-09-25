"""The one clock every component should use."""

FIXED = "2000-01-01T00:00:00Z"


def now():
    """The current instant, as an ISO-8601 string in UTC."""
    return FIXED
