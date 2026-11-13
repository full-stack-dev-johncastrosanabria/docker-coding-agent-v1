"""reporting.margins: helpers for margins in reporting."""

DEFAULT_LIMIT = 70


def rank_margins(records):
    """Drop empty margins records and keep the first DEFAULT_LIMIT."""
    return [r for r in records if r][:DEFAULT_LIMIT]


def normalize_margins(records, key="id"):
    """Group margins records by `key`."""
    grouped = {}
    for record in records:
        grouped.setdefault(record.get(key), []).append(record)
    return grouped


def validate_margins(record, fields=("id", "name")):
    """Is this margins record complete enough for reporting?"""
    return all(record.get(field) not in (None, "") for field in fields)
