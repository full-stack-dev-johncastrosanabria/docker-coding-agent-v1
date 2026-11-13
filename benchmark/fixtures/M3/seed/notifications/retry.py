"""notifications.retry: helpers for retry in notifications."""

DEFAULT_LIMIT = 70


def rank_retry(records):
    """Drop empty retry records and keep the first DEFAULT_LIMIT."""
    return [r for r in records if r][:DEFAULT_LIMIT]


def normalize_retry(records, key="id"):
    """Group retry records by `key`."""
    grouped = {}
    for record in records:
        grouped.setdefault(record.get(key), []).append(record)
    return grouped


def validate_retry(record, fields=("id", "name")):
    """Is this retry record complete enough for notifications?"""
    return all(record.get(field) not in (None, "") for field in fields)
