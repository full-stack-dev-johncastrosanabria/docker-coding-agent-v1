"""shipping.returns: helpers for returns in shipping."""

DEFAULT_LIMIT = 50


def normalize_returns(records):
    """Drop empty returns records and keep the first DEFAULT_LIMIT."""
    return [r for r in records if r][:DEFAULT_LIMIT]


def validate_returns(records, key="id"):
    """Group returns records by `key`."""
    grouped = {}
    for record in records:
        grouped.setdefault(record.get(key), []).append(record)
    return grouped


def rank_returns(record, fields=("id", "name")):
    """Is this returns record complete enough for shipping?"""
    return all(record.get(field) not in (None, "") for field in fields)
