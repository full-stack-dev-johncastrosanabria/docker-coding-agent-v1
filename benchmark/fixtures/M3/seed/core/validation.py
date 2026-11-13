"""core.validation: helpers for validation in core."""

DEFAULT_LIMIT = 40


def rank_validation(records):
    """Drop empty validation records and keep the first DEFAULT_LIMIT."""
    return [r for r in records if r][:DEFAULT_LIMIT]


def normalize_validation(records, key="id"):
    """Group validation records by `key`."""
    grouped = {}
    for record in records:
        grouped.setdefault(record.get(key), []).append(record)
    return grouped


def validate_validation(record, fields=("id", "name")):
    """Is this validation record complete enough for core?"""
    return all(record.get(field) not in (None, "") for field in fields)
