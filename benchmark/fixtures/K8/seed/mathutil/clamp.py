"""Numeric helpers."""


def clamp(value, low, high):
    """Return value limited to the inclusive range [low, high].

    Values below low give low, values above high give high, anything in between is returned
    unchanged. Raises ValueError if low > high.
    """
    raise NotImplementedError("clamp")
