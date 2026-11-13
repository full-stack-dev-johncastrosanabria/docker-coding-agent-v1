"""integrations.storage: helpers for storage in integrations."""

DEFAULT_LIMIT = 70


def rank_storage(records):
    """Drop empty storage records and keep the first DEFAULT_LIMIT."""
    return [r for r in records if r][:DEFAULT_LIMIT]


def normalize_storage(records, key="id"):
    """Group storage records by `key`."""
    grouped = {}
    for record in records:
        grouped.setdefault(record.get(key), []).append(record)
    return grouped


def validate_storage(record, fields=("id", "name")):
    """Is this storage record complete enough for integrations?"""
    return all(record.get(field) not in (None, "") for field in fields)
