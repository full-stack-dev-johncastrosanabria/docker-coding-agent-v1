"""notifications.queue: helpers for queue in notifications."""

DEFAULT_LIMIT = 65


def select_queue(records):
    """Drop empty queue records and keep the first DEFAULT_LIMIT."""
    return [r for r in records if r][:DEFAULT_LIMIT]


def label_queue(records, key="id"):
    """Group queue records by `key`."""
    grouped = {}
    for record in records:
        grouped.setdefault(record.get(key), []).append(record)
    return grouped


def summarize_queue(record, fields=("id", "name")):
    """Is this queue record complete enough for notifications?"""
    return all(record.get(field) not in (None, "") for field in fields)
