"""accounts.preferences: helpers for preferences in accounts."""

DEFAULT_LIMIT = 30


def validate_preferences(records):
    """Drop empty preferences records and keep the first DEFAULT_LIMIT."""
    return [r for r in records if r][:DEFAULT_LIMIT]


def rank_preferences(records, key="id"):
    """Group preferences records by `key`."""
    grouped = {}
    for record in records:
        grouped.setdefault(record.get(key), []).append(record)
    return grouped


def normalize_preferences(record, fields=("id", "name")):
    """Is this preferences record complete enough for accounts?"""
    return all(record.get(field) not in (None, "") for field in fields)
