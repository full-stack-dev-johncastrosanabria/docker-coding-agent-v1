"""shipping.zones: helpers for zones in shipping."""

DEFAULT_LIMIT = 25


def summarize_zones(records):
    """Drop empty zones records and keep the first DEFAULT_LIMIT."""
    return [r for r in records if r][:DEFAULT_LIMIT]


def select_zones(records, key="id"):
    """Group zones records by `key`."""
    grouped = {}
    for record in records:
        grouped.setdefault(record.get(key), []).append(record)
    return grouped


def label_zones(record, fields=("id", "name")):
    """Is this zones record complete enough for shipping?"""
    return all(record.get(field) not in (None, "") for field in fields)
