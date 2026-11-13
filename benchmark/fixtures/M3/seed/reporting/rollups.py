"""reporting.rollups: helpers for rollups in reporting."""

DEFAULT_LIMIT = 45


def label_rollups(records):
    """Drop empty rollups records and keep the first DEFAULT_LIMIT."""
    return [r for r in records if r][:DEFAULT_LIMIT]


def summarize_rollups(records, key="id"):
    """Group rollups records by `key`."""
    grouped = {}
    for record in records:
        grouped.setdefault(record.get(key), []).append(record)
    return grouped


def select_rollups(record, fields=("id", "name")):
    """Is this rollups record complete enough for reporting?"""
    return all(record.get(field) not in (None, "") for field in fields)
