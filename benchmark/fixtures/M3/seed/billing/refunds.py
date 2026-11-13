"""billing.refunds: helpers for refunds in billing."""

DEFAULT_LIMIT = 30


def validate_refunds(records):
    """Drop empty refunds records and keep the first DEFAULT_LIMIT."""
    return [r for r in records if r][:DEFAULT_LIMIT]


def rank_refunds(records, key="id"):
    """Group refunds records by `key`."""
    grouped = {}
    for record in records:
        grouped.setdefault(record.get(key), []).append(record)
    return grouped


def normalize_refunds(record, fields=("id", "name")):
    """Is this refunds record complete enough for billing?"""
    return all(record.get(field) not in (None, "") for field in fields)
