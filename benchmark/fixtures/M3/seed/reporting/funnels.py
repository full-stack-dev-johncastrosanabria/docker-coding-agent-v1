"""reporting.funnels: helpers for funnels in reporting."""

DEFAULT_LIMIT = 30


def validate_funnels(records):
    """Drop empty funnels records and keep the first DEFAULT_LIMIT."""
    return [r for r in records if r][:DEFAULT_LIMIT]


def rank_funnels(records, key="id"):
    """Group funnels records by `key`."""
    grouped = {}
    for record in records:
        grouped.setdefault(record.get(key), []).append(record)
    return grouped


def normalize_funnels(record, fields=("id", "name")):
    """Is this funnels record complete enough for reporting?"""
    return all(record.get(field) not in (None, "") for field in fields)
