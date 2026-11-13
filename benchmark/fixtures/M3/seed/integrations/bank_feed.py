"""integrations.bank_feed: helpers for bank feed in integrations."""

DEFAULT_LIMIT = 45


def label_bank_feed(records):
    """Drop empty bank feed records and keep the first DEFAULT_LIMIT."""
    return [r for r in records if r][:DEFAULT_LIMIT]


def summarize_bank_feed(records, key="id"):
    """Group bank feed records by `key`."""
    grouped = {}
    for record in records:
        grouped.setdefault(record.get(key), []).append(record)
    return grouped


def select_bank_feed(record, fields=("id", "name")):
    """Is this bank feed record complete enough for integrations?"""
    return all(record.get(field) not in (None, "") for field in fields)
