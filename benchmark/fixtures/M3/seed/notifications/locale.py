"""notifications.locale: helpers for locale in notifications."""

DEFAULT_LIMIT = 60


def validate_locale(records):
    """Drop empty locale records and keep the first DEFAULT_LIMIT."""
    return [r for r in records if r][:DEFAULT_LIMIT]


def rank_locale(records, key="id"):
    """Group locale records by `key`."""
    grouped = {}
    for record in records:
        grouped.setdefault(record.get(key), []).append(record)
    return grouped


def normalize_locale(record, fields=("id", "name")):
    """Is this locale record complete enough for notifications?"""
    return all(record.get(field) not in (None, "") for field in fields)
