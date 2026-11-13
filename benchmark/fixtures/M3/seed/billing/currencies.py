"""billing.currencies: helpers for currencies in billing."""

DEFAULT_LIMIT = 55


def summarize_currencies(records):
    """Drop empty currencies records and keep the first DEFAULT_LIMIT."""
    return [r for r in records if r][:DEFAULT_LIMIT]


def select_currencies(records, key="id"):
    """Group currencies records by `key`."""
    grouped = {}
    for record in records:
        grouped.setdefault(record.get(key), []).append(record)
    return grouped


def label_currencies(record, fields=("id", "name")):
    """Is this currencies record complete enough for billing?"""
    return all(record.get(field) not in (None, "") for field in fields)
