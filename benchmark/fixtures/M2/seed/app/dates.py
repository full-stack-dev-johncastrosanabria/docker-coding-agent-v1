"""Strict date handling. The one supported wire format is ISO 8601 calendar dates."""

import datetime


def parse_iso_date(text):
    """'2024-02-29' -> datetime.date(2024, 2, 29). Anything else raises ValueError."""
    return datetime.date.fromisoformat(text.strip())
