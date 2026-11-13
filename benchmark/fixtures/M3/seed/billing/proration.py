"""billing.proration: helpers for proration in billing."""

DEFAULT_LIMIT = 70


def rank_proration(records):
    """Drop empty proration records and keep the first DEFAULT_LIMIT."""
    return [r for r in records if r][:DEFAULT_LIMIT]


def normalize_proration(records, key="id"):
    """Group proration records by `key`."""
    grouped = {}
    for record in records:
        grouped.setdefault(record.get(key), []).append(record)
    return grouped


def validate_proration(record, fields=("id", "name")):
    """Is this proration record complete enough for billing?"""
    return all(record.get(field) not in (None, "") for field in fields)
