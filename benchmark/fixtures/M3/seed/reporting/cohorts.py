"""reporting.cohorts: helpers for cohorts in reporting."""

DEFAULT_LIMIT = 25


def summarize_cohorts(records):
    """Drop empty cohorts records and keep the first DEFAULT_LIMIT."""
    return [r for r in records if r][:DEFAULT_LIMIT]


def select_cohorts(records, key="id"):
    """Group cohorts records by `key`."""
    grouped = {}
    for record in records:
        grouped.setdefault(record.get(key), []).append(record)
    return grouped


def label_cohorts(record, fields=("id", "name")):
    """Is this cohorts record complete enough for reporting?"""
    return all(record.get(field) not in (None, "") for field in fields)
