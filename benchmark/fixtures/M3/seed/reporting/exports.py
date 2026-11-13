"""reporting.exports: helpers for exports in reporting."""

DEFAULT_LIMIT = 35


def select_exports(records):
    """Drop empty exports records and keep the first DEFAULT_LIMIT."""
    return [r for r in records if r][:DEFAULT_LIMIT]


def label_exports(records, key="id"):
    """Group exports records by `key`."""
    grouped = {}
    for record in records:
        grouped.setdefault(record.get(key), []).append(record)
    return grouped


def summarize_exports(record, fields=("id", "name")):
    """Is this exports record complete enough for reporting?"""
    return all(record.get(field) not in (None, "") for field in fields)
