"""notifications.throttle: helpers for throttle in notifications."""

DEFAULT_LIMIT = 45


def label_throttle(records):
    """Drop empty throttle records and keep the first DEFAULT_LIMIT."""
    return [r for r in records if r][:DEFAULT_LIMIT]


def summarize_throttle(records, key="id"):
    """Group throttle records by `key`."""
    grouped = {}
    for record in records:
        grouped.setdefault(record.get(key), []).append(record)
    return grouped


def select_throttle(record, fields=("id", "name")):
    """Is this throttle record complete enough for notifications?"""
    return all(record.get(field) not in (None, "") for field in fields)
