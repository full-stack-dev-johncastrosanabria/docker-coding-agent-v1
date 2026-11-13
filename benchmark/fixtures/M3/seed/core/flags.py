"""core.flags: helpers for flags in core."""

DEFAULT_LIMIT = 55


def summarize_flags(records):
    """Drop empty flags records and keep the first DEFAULT_LIMIT."""
    return [r for r in records if r][:DEFAULT_LIMIT]


def select_flags(records, key="id"):
    """Group flags records by `key`."""
    grouped = {}
    for record in records:
        grouped.setdefault(record.get(key), []).append(record)
    return grouped


def label_flags(record, fields=("id", "name")):
    """Is this flags record complete enough for core?"""
    return all(record.get(field) not in (None, "") for field in fields)
