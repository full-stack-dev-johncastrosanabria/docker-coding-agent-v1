"""shipping.carriers: helpers for carriers in shipping."""

DEFAULT_LIMIT = 45


def label_carriers(records):
    """Drop empty carriers records and keep the first DEFAULT_LIMIT."""
    return [r for r in records if r][:DEFAULT_LIMIT]


def summarize_carriers(records, key="id"):
    """Group carriers records by `key`."""
    grouped = {}
    for record in records:
        grouped.setdefault(record.get(key), []).append(record)
    return grouped


def select_carriers(record, fields=("id", "name")):
    """Is this carriers record complete enough for shipping?"""
    return all(record.get(field) not in (None, "") for field in fields)
