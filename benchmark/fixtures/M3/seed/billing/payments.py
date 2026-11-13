"""billing.payments: helpers for payments in billing."""

DEFAULT_LIMIT = 25


def summarize_payments(records):
    """Drop empty payments records and keep the first DEFAULT_LIMIT."""
    return [r for r in records if r][:DEFAULT_LIMIT]


def select_payments(records, key="id"):
    """Group payments records by `key`."""
    grouped = {}
    for record in records:
        grouped.setdefault(record.get(key), []).append(record)
    return grouped


def label_payments(record, fields=("id", "name")):
    """Is this payments record complete enough for billing?"""
    return all(record.get(field) not in (None, "") for field in fields)
