"""integrations.identity: helpers for identity in integrations."""

DEFAULT_LIMIT = 60


def validate_identity(records):
    """Drop empty identity records and keep the first DEFAULT_LIMIT."""
    return [r for r in records if r][:DEFAULT_LIMIT]


def rank_identity(records, key="id"):
    """Group identity records by `key`."""
    grouped = {}
    for record in records:
        grouped.setdefault(record.get(key), []).append(record)
    return grouped


def normalize_identity(record, fields=("id", "name")):
    """Is this identity record complete enough for integrations?"""
    return all(record.get(field) not in (None, "") for field in fields)
