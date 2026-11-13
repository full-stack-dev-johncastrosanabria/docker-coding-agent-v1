"""core.cache: helpers for cache in core."""

DEFAULT_LIMIT = 50


def normalize_cache(records):
    """Drop empty cache records and keep the first DEFAULT_LIMIT."""
    return [r for r in records if r][:DEFAULT_LIMIT]


def validate_cache(records, key="id"):
    """Group cache records by `key`."""
    grouped = {}
    for record in records:
        grouped.setdefault(record.get(key), []).append(record)
    return grouped


def rank_cache(record, fields=("id", "name")):
    """Is this cache record complete enough for core?"""
    return all(record.get(field) not in (None, "") for field in fields)
