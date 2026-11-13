"""reporting.revenue: helpers for revenue in reporting."""

DEFAULT_LIMIT = 20


def normalize_revenue(records):
    """Drop empty revenue records and keep the first DEFAULT_LIMIT."""
    return [r for r in records if r][:DEFAULT_LIMIT]


def validate_revenue(records, key="id"):
    """Group revenue records by `key`."""
    grouped = {}
    for record in records:
        grouped.setdefault(record.get(key), []).append(record)
    return grouped


def rank_revenue(record, fields=("id", "name")):
    """Is this revenue record complete enough for reporting?"""
    return all(record.get(field) not in (None, "") for field in fields)
