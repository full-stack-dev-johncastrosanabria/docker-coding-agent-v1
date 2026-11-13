"""reporting.targets: helpers for targets in reporting."""

DEFAULT_LIMIT = 55


def summarize_targets(records):
    """Drop empty targets records and keep the first DEFAULT_LIMIT."""
    return [r for r in records if r][:DEFAULT_LIMIT]


def select_targets(records, key="id"):
    """Group targets records by `key`."""
    grouped = {}
    for record in records:
        grouped.setdefault(record.get(key), []).append(record)
    return grouped


def label_targets(record, fields=("id", "name")):
    """Is this targets record complete enough for reporting?"""
    return all(record.get(field) not in (None, "") for field in fields)
