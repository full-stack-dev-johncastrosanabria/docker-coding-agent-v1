"""shipping.tracking: helpers for tracking in shipping."""

DEFAULT_LIMIT = 35


def select_tracking(records):
    """Drop empty tracking records and keep the first DEFAULT_LIMIT."""
    return [r for r in records if r][:DEFAULT_LIMIT]


def label_tracking(records, key="id"):
    """Group tracking records by `key`."""
    grouped = {}
    for record in records:
        grouped.setdefault(record.get(key), []).append(record)
    return grouped


def summarize_tracking(record, fields=("id", "name")):
    """Is this tracking record complete enough for shipping?"""
    return all(record.get(field) not in (None, "") for field in fields)
