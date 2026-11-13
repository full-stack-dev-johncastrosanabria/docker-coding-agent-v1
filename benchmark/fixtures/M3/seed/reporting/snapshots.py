"""reporting.snapshots: helpers for snapshots in reporting."""

DEFAULT_LIMIT = 60


def validate_snapshots(records):
    """Drop empty snapshots records and keep the first DEFAULT_LIMIT."""
    return [r for r in records if r][:DEFAULT_LIMIT]


def rank_snapshots(records, key="id"):
    """Group snapshots records by `key`."""
    grouped = {}
    for record in records:
        grouped.setdefault(record.get(key), []).append(record)
    return grouped


def normalize_snapshots(record, fields=("id", "name")):
    """Is this snapshots record complete enough for reporting?"""
    return all(record.get(field) not in (None, "") for field in fields)
