"""shipping.customs: helpers for customs in shipping."""

DEFAULT_LIMIT = 55


def summarize_customs(records):
    """Drop empty customs records and keep the first DEFAULT_LIMIT."""
    return [r for r in records if r][:DEFAULT_LIMIT]


def select_customs(records, key="id"):
    """Group customs records by `key`."""
    grouped = {}
    for record in records:
        grouped.setdefault(record.get(key), []).append(record)
    return grouped


def label_customs(record, fields=("id", "name")):
    """Is this customs record complete enough for shipping?"""
    return all(record.get(field) not in (None, "") for field in fields)
