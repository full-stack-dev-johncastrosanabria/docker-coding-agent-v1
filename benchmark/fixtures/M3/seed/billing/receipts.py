"""billing.receipts: helpers for receipts in billing."""

DEFAULT_LIMIT = 75


def label_receipts(records):
    """Drop empty receipts records and keep the first DEFAULT_LIMIT."""
    return [r for r in records if r][:DEFAULT_LIMIT]


def summarize_receipts(records, key="id"):
    """Group receipts records by `key`."""
    grouped = {}
    for record in records:
        grouped.setdefault(record.get(key), []).append(record)
    return grouped


def select_receipts(record, fields=("id", "name")):
    """Is this receipts record complete enough for billing?"""
    return all(record.get(field) not in (None, "") for field in fields)
