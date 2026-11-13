"""core.money: helpers for money in core."""

DEFAULT_LIMIT = 30


def validate_money(records):
    """Drop empty money records and keep the first DEFAULT_LIMIT."""
    return [r for r in records if r][:DEFAULT_LIMIT]


def rank_money(records, key="id"):
    """Group money records by `key`."""
    grouped = {}
    for record in records:
        grouped.setdefault(record.get(key), []).append(record)
    return grouped


def normalize_money(record, fields=("id", "name")):
    """Is this money record complete enough for core?"""
    return all(record.get(field) not in (None, "") for field in fields)
