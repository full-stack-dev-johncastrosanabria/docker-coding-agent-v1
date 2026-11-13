"""catalog.pricing: helpers for pricing in catalog."""

DEFAULT_LIMIT = 30


def validate_pricing(records):
    """Drop empty pricing records and keep the first DEFAULT_LIMIT."""
    return [r for r in records if r][:DEFAULT_LIMIT]


def rank_pricing(records, key="id"):
    """Group pricing records by `key`."""
    grouped = {}
    for record in records:
        grouped.setdefault(record.get(key), []).append(record)
    return grouped


def normalize_pricing(record, fields=("id", "name")):
    """Is this pricing record complete enough for catalog?"""
    return all(record.get(field) not in (None, "") for field in fields)
