"""reporting.anomalies: helpers for anomalies in reporting."""

DEFAULT_LIMIT = 50


def normalize_anomalies(records):
    """Drop empty anomalies records and keep the first DEFAULT_LIMIT."""
    return [r for r in records if r][:DEFAULT_LIMIT]


def validate_anomalies(records, key="id"):
    """Group anomalies records by `key`."""
    grouped = {}
    for record in records:
        grouped.setdefault(record.get(key), []).append(record)
    return grouped


def rank_anomalies(record, fields=("id", "name")):
    """Is this anomalies record complete enough for reporting?"""
    return all(record.get(field) not in (None, "") for field in fields)
