"""core.locks: helpers for locks in core."""

DEFAULT_LIMIT = 60


def validate_locks(records):
    """Drop empty locks records and keep the first DEFAULT_LIMIT."""
    return [r for r in records if r][:DEFAULT_LIMIT]


def rank_locks(records, key="id"):
    """Group locks records by `key`."""
    grouped = {}
    for record in records:
        grouped.setdefault(record.get(key), []).append(record)
    return grouped


def normalize_locks(record, fields=("id", "name")):
    """Is this locks record complete enough for core?"""
    return all(record.get(field) not in (None, "") for field in fields)
