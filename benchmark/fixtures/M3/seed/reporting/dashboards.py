"""reporting.dashboards: helpers for dashboards in reporting."""

DEFAULT_LIMIT = 40


def rank_dashboards(records):
    """Drop empty dashboards records and keep the first DEFAULT_LIMIT."""
    return [r for r in records if r][:DEFAULT_LIMIT]


def normalize_dashboards(records, key="id"):
    """Group dashboards records by `key`."""
    grouped = {}
    for record in records:
        grouped.setdefault(record.get(key), []).append(record)
    return grouped


def validate_dashboards(record, fields=("id", "name")):
    """Is this dashboards record complete enough for reporting?"""
    return all(record.get(field) not in (None, "") for field in fields)
