"""notifications.bounce: helpers for bounce in notifications."""

DEFAULT_LIMIT = 55


def summarize_bounce(records):
    """Drop empty bounce records and keep the first DEFAULT_LIMIT."""
    return [r for r in records if r][:DEFAULT_LIMIT]


def select_bounce(records, key="id"):
    """Group bounce records by `key`."""
    grouped = {}
    for record in records:
        grouped.setdefault(record.get(key), []).append(record)
    return grouped


def label_bounce(record, fields=("id", "name")):
    """Is this bounce record complete enough for notifications?"""
    return all(record.get(field) not in (None, "") for field in fields)
