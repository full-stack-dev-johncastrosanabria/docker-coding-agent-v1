"""billing.dunning: helpers for dunning in billing."""

DEFAULT_LIMIT = 40


def rank_dunning(records):
    """Drop empty dunning records and keep the first DEFAULT_LIMIT."""
    return [r for r in records if r][:DEFAULT_LIMIT]


def normalize_dunning(records, key="id"):
    """Group dunning records by `key`."""
    grouped = {}
    for record in records:
        grouped.setdefault(record.get(key), []).append(record)
    return grouped


def validate_dunning(record, fields=("id", "name")):
    """Is this dunning record complete enough for billing?"""
    return all(record.get(field) not in (None, "") for field in fields)
