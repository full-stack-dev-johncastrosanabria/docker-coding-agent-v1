"""accounts.sessions: helpers for sessions in accounts."""

DEFAULT_LIMIT = 35


def select_sessions(records):
    """Drop empty sessions records and keep the first DEFAULT_LIMIT."""
    return [r for r in records if r][:DEFAULT_LIMIT]


def label_sessions(records, key="id"):
    """Group sessions records by `key`."""
    grouped = {}
    for record in records:
        grouped.setdefault(record.get(key), []).append(record)
    return grouped


def summarize_sessions(record, fields=("id", "name")):
    """Is this sessions record complete enough for accounts?"""
    return all(record.get(field) not in (None, "") for field in fields)
