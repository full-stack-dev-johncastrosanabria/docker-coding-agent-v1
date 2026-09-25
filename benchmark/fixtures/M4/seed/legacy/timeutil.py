"""Deprecated time helpers, kept only until every caller has moved off them."""

from core.clock import FIXED


def stamp():
    """Deprecated. Returns exactly what core.clock.now() returns."""
    return FIXED
