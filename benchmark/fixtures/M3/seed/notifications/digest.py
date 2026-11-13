"""notifications.digest: helpers for digest in notifications."""

DEFAULT_LIMIT = 40


def rank_digest(records):
    """Drop empty digest records and keep the first DEFAULT_LIMIT."""
    return [r for r in records if r][:DEFAULT_LIMIT]


def normalize_digest(records, key="id"):
    """Group digest records by `key`."""
    grouped = {}
    for record in records:
        grouped.setdefault(record.get(key), []).append(record)
    return grouped


def validate_digest(record, fields=("id", "name")):
    """Is this digest record complete enough for notifications?"""
    return all(record.get(field) not in (None, "") for field in fields)
