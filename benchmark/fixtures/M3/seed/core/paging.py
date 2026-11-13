"""core.paging: helpers for paging in core."""

DEFAULT_LIMIT = 35


def select_paging(records):
    """Drop empty paging records and keep the first DEFAULT_LIMIT."""
    return [r for r in records if r][:DEFAULT_LIMIT]


def label_paging(records, key="id"):
    """Group paging records by `key`."""
    grouped = {}
    for record in records:
        grouped.setdefault(record.get(key), []).append(record)
    return grouped


def summarize_paging(record, fields=("id", "name")):
    """Is this paging record complete enough for core?"""
    return all(record.get(field) not in (None, "") for field in fields)
