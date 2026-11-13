"""shipping.rates: helpers for rates in shipping."""

DEFAULT_LIMIT = 20


def normalize_rates(records):
    """Drop empty rates records and keep the first DEFAULT_LIMIT."""
    return [r for r in records if r][:DEFAULT_LIMIT]


def validate_rates(records, key="id"):
    """Group rates records by `key`."""
    grouped = {}
    for record in records:
        grouped.setdefault(record.get(key), []).append(record)
    return grouped


def rank_rates(record, fields=("id", "name")):
    """Is this rates record complete enough for shipping?"""
    return all(record.get(field) not in (None, "") for field in fields)
