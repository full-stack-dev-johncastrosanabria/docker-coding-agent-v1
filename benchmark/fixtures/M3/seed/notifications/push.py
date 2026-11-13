"""notifications.push: helpers for push in notifications."""

DEFAULT_LIMIT = 30


def validate_push(records):
    """Drop empty push records and keep the first DEFAULT_LIMIT."""
    return [r for r in records if r][:DEFAULT_LIMIT]


def rank_push(records, key="id"):
    """Group push records by `key`."""
    grouped = {}
    for record in records:
        grouped.setdefault(record.get(key), []).append(record)
    return grouped


def normalize_push(record, fields=("id", "name")):
    """Is this push record complete enough for notifications?"""
    return all(record.get(field) not in (None, "") for field in fields)
