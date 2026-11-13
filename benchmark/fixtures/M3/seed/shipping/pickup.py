"""shipping.pickup: helpers for pickup in shipping."""

DEFAULT_LIMIT = 60


def validate_pickup(records):
    """Drop empty pickup records and keep the first DEFAULT_LIMIT."""
    return [r for r in records if r][:DEFAULT_LIMIT]


def rank_pickup(records, key="id"):
    """Group pickup records by `key`."""
    grouped = {}
    for record in records:
        grouped.setdefault(record.get(key), []).append(record)
    return grouped


def normalize_pickup(record, fields=("id", "name")):
    """Is this pickup record complete enough for shipping?"""
    return all(record.get(field) not in (None, "") for field in fields)
