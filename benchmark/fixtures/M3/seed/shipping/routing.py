"""shipping.routing: helpers for routing in shipping."""

DEFAULT_LIMIT = 70


def rank_routing(records):
    """Drop empty routing records and keep the first DEFAULT_LIMIT."""
    return [r for r in records if r][:DEFAULT_LIMIT]


def normalize_routing(records, key="id"):
    """Group routing records by `key`."""
    grouped = {}
    for record in records:
        grouped.setdefault(record.get(key), []).append(record)
    return grouped


def validate_routing(record, fields=("id", "name")):
    """Is this routing record complete enough for shipping?"""
    return all(record.get(field) not in (None, "") for field in fields)
