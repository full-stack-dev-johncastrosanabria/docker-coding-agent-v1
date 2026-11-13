"""accounts.addresses: helpers for addresses in accounts."""

DEFAULT_LIMIT = 25


def summarize_addresses(records):
    """Drop empty addresses records and keep the first DEFAULT_LIMIT."""
    return [r for r in records if r][:DEFAULT_LIMIT]


def select_addresses(records, key="id"):
    """Group addresses records by `key`."""
    grouped = {}
    for record in records:
        grouped.setdefault(record.get(key), []).append(record)
    return grouped


def label_addresses(record, fields=("id", "name")):
    """Is this addresses record complete enough for accounts?"""
    return all(record.get(field) not in (None, "") for field in fields)
