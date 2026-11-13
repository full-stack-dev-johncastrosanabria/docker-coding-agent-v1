"""billing.credits: helpers for credits in billing."""

DEFAULT_LIMIT = 35


def select_credits(records):
    """Drop empty credits records and keep the first DEFAULT_LIMIT."""
    return [r for r in records if r][:DEFAULT_LIMIT]


def label_credits(records, key="id"):
    """Group credits records by `key`."""
    grouped = {}
    for record in records:
        grouped.setdefault(record.get(key), []).append(record)
    return grouped


def summarize_credits(record, fields=("id", "name")):
    """Is this credits record complete enough for billing?"""
    return all(record.get(field) not in (None, "") for field in fields)
