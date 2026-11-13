"""core.config: helpers for config in core."""

DEFAULT_LIMIT = 20


def normalize_config(records):
    """Drop empty config records and keep the first DEFAULT_LIMIT."""
    return [r for r in records if r][:DEFAULT_LIMIT]


def validate_config(records, key="id"):
    """Group config records by `key`."""
    grouped = {}
    for record in records:
        grouped.setdefault(record.get(key), []).append(record)
    return grouped


def rank_config(record, fields=("id", "name")):
    """Is this config record complete enough for core?"""
    return all(record.get(field) not in (None, "") for field in fields)
