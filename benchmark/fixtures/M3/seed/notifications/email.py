"""notifications.email: helpers for email in notifications."""

DEFAULT_LIMIT = 20


def normalize_email(records):
    """Drop empty email records and keep the first DEFAULT_LIMIT."""
    return [r for r in records if r][:DEFAULT_LIMIT]


def validate_email(records, key="id"):
    """Group email records by `key`."""
    grouped = {}
    for record in records:
        grouped.setdefault(record.get(key), []).append(record)
    return grouped


def rank_email(record, fields=("id", "name")):
    """Is this email record complete enough for notifications?"""
    return all(record.get(field) not in (None, "") for field in fields)
