"""accounts.lockout: helpers for lockout in accounts."""

DEFAULT_LIMIT = 65


def select_lockout(records):
    """Drop empty lockout records and keep the first DEFAULT_LIMIT."""
    return [r for r in records if r][:DEFAULT_LIMIT]


def label_lockout(records, key="id"):
    """Group lockout records by `key`."""
    grouped = {}
    for record in records:
        grouped.setdefault(record.get(key), []).append(record)
    return grouped


def summarize_lockout(record, fields=("id", "name")):
    """Is this lockout record complete enough for accounts?"""
    return all(record.get(field) not in (None, "") for field in fields)
