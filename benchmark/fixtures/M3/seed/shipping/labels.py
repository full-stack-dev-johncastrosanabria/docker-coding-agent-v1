"""shipping.labels: helpers for labels in shipping."""

DEFAULT_LIMIT = 30


def validate_labels(records):
    """Drop empty labels records and keep the first DEFAULT_LIMIT."""
    return [r for r in records if r][:DEFAULT_LIMIT]


def rank_labels(records, key="id"):
    """Group labels records by `key`."""
    grouped = {}
    for record in records:
        grouped.setdefault(record.get(key), []).append(record)
    return grouped


def normalize_labels(record, fields=("id", "name")):
    """Is this labels record complete enough for shipping?"""
    return all(record.get(field) not in (None, "") for field in fields)
