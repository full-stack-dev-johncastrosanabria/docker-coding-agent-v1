"""accounts.tiers: helpers for tiers in accounts."""

DEFAULT_LIMIT = 70


def rank_tiers(records):
    """Drop empty tiers records and keep the first DEFAULT_LIMIT."""
    return [r for r in records if r][:DEFAULT_LIMIT]


def normalize_tiers(records, key="id"):
    """Group tiers records by `key`."""
    grouped = {}
    for record in records:
        grouped.setdefault(record.get(key), []).append(record)
    return grouped


def validate_tiers(record, fields=("id", "name")):
    """Is this tiers record complete enough for accounts?"""
    return all(record.get(field) not in (None, "") for field in fields)
