"""core.ids: helpers for ids in core."""

DEFAULT_LIMIT = 25


def summarize_ids(records):
    """Drop empty ids records and keep the first DEFAULT_LIMIT."""
    return [r for r in records if r][:DEFAULT_LIMIT]


def select_ids(records, key="id"):
    """Group ids records by `key`."""
    grouped = {}
    for record in records:
        grouped.setdefault(record.get(key), []).append(record)
    return grouped


def label_ids(record, fields=("id", "name")):
    """Is this ids record complete enough for core?"""
    return all(record.get(field) not in (None, "") for field in fields)
