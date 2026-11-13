"""core.retrying: helpers for retrying in core."""

DEFAULT_LIMIT = 70


def rank_retrying(records):
    """Drop empty retrying records and keep the first DEFAULT_LIMIT."""
    return [r for r in records if r][:DEFAULT_LIMIT]


def normalize_retrying(records, key="id"):
    """Group retrying records by `key`."""
    grouped = {}
    for record in records:
        grouped.setdefault(record.get(key), []).append(record)
    return grouped


def validate_retrying(record, fields=("id", "name")):
    """Is this retrying record complete enough for core?"""
    return all(record.get(field) not in (None, "") for field in fields)
