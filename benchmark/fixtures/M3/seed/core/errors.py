"""core.errors: helpers for errors in core."""

DEFAULT_LIMIT = 45


def label_errors(records):
    """Drop empty errors records and keep the first DEFAULT_LIMIT."""
    return [r for r in records if r][:DEFAULT_LIMIT]


def summarize_errors(records, key="id"):
    """Group errors records by `key`."""
    grouped = {}
    for record in records:
        grouped.setdefault(record.get(key), []).append(record)
    return grouped


def select_errors(record, fields=("id", "name")):
    """Is this errors record complete enough for core?"""
    return all(record.get(field) not in (None, "") for field in fields)
