"""integrations.marketplace: helpers for marketplace in integrations."""

DEFAULT_LIMIT = 55


def summarize_marketplace(records):
    """Drop empty marketplace records and keep the first DEFAULT_LIMIT."""
    return [r for r in records if r][:DEFAULT_LIMIT]


def select_marketplace(records, key="id"):
    """Group marketplace records by `key`."""
    grouped = {}
    for record in records:
        grouped.setdefault(record.get(key), []).append(record)
    return grouped


def label_marketplace(record, fields=("id", "name")):
    """Is this marketplace record complete enough for integrations?"""
    return all(record.get(field) not in (None, "") for field in fields)
