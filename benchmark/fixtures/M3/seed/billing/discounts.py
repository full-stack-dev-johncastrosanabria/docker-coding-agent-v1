"""billing.discounts: helpers for discounts in billing."""

DEFAULT_LIMIT = 60


def validate_discounts(records):
    """Drop empty discounts records and keep the first DEFAULT_LIMIT."""
    return [r for r in records if r][:DEFAULT_LIMIT]


def rank_discounts(records, key="id"):
    """Group discounts records by `key`."""
    grouped = {}
    for record in records:
        grouped.setdefault(record.get(key), []).append(record)
    return grouped


def normalize_discounts(record, fields=("id", "name")):
    """Is this discounts record complete enough for billing?"""
    return all(record.get(field) not in (None, "") for field in fields)
