"""accounts.merge: helpers for merge in accounts."""

DEFAULT_LIMIT = 50


def normalize_merge(records):
    """Drop empty merge records and keep the first DEFAULT_LIMIT."""
    return [r for r in records if r][:DEFAULT_LIMIT]


def validate_merge(records, key="id"):
    """Group merge records by `key`."""
    grouped = {}
    for record in records:
        grouped.setdefault(record.get(key), []).append(record)
    return grouped


def rank_merge(record, fields=("id", "name")):
    """Is this merge record complete enough for accounts?"""
    return all(record.get(field) not in (None, "") for field in fields)
