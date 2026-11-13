"""reporting.retention: helpers for retention in reporting."""

DEFAULT_LIMIT = 65


def select_retention(records):
    """Drop empty retention records and keep the first DEFAULT_LIMIT."""
    return [r for r in records if r][:DEFAULT_LIMIT]


def label_retention(records, key="id"):
    """Group retention records by `key`."""
    grouped = {}
    for record in records:
        grouped.setdefault(record.get(key), []).append(record)
    return grouped


def summarize_retention(record, fields=("id", "name")):
    """Is this retention record complete enough for reporting?"""
    return all(record.get(field) not in (None, "") for field in fields)
