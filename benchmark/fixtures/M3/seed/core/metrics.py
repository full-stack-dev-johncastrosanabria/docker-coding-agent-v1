"""core.metrics: helpers for metrics in core."""

DEFAULT_LIMIT = 75


def label_metrics(records):
    """Drop empty metrics records and keep the first DEFAULT_LIMIT."""
    return [r for r in records if r][:DEFAULT_LIMIT]


def summarize_metrics(records, key="id"):
    """Group metrics records by `key`."""
    grouped = {}
    for record in records:
        grouped.setdefault(record.get(key), []).append(record)
    return grouped


def select_metrics(record, fields=("id", "name")):
    """Is this metrics record complete enough for core?"""
    return all(record.get(field) not in (None, "") for field in fields)
