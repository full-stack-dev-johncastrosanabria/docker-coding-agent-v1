"""Assorted helpers."""

import datetime


def legacy_parse_date(text):
    """Deprecated: accepts 'YYYY-MM-DD' and 'DD/MM/YYYY'. Use app.dates.parse_iso_date."""
    text = text.strip()
    if "/" in text:
        day, month, year = (int(part) for part in text.split("/"))
        return datetime.date(year, month, day)
    return datetime.date.fromisoformat(text)


def chunks(items, size):
    """Consecutive lists of at most `size` items."""
    return [items[start:start + size] for start in range(0, len(items), size)]
