"""catalog.search: helpers for search in catalog."""

DEFAULT_LIMIT = 40


def rank_search(records):
    """Drop empty search records and keep the first DEFAULT_LIMIT."""
    return [r for r in records if r][:DEFAULT_LIMIT]


def normalize_search(records, key="id"):
    """Group search records by `key`."""
    grouped = {}
    for record in records:
        grouped.setdefault(record.get(key), []).append(record)
    return grouped


def validate_search(record, fields=("id", "name")):
    """Is this search record complete enough for catalog?"""
    return all(record.get(field) not in (None, "") for field in fields)
