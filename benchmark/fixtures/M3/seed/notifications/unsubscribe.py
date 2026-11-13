"""notifications.unsubscribe: helpers for unsubscribe in notifications."""

DEFAULT_LIMIT = 50


def normalize_unsubscribe(records):
    """Drop empty unsubscribe records and keep the first DEFAULT_LIMIT."""
    return [r for r in records if r][:DEFAULT_LIMIT]


def validate_unsubscribe(records, key="id"):
    """Group unsubscribe records by `key`."""
    grouped = {}
    for record in records:
        grouped.setdefault(record.get(key), []).append(record)
    return grouped


def rank_unsubscribe(record, fields=("id", "name")):
    """Is this unsubscribe record complete enough for notifications?"""
    return all(record.get(field) not in (None, "") for field in fields)
