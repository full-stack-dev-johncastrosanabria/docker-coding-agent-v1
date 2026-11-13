"""reporting.forecast: helpers for forecast in reporting."""

DEFAULT_LIMIT = 75


def label_forecast(records):
    """Drop empty forecast records and keep the first DEFAULT_LIMIT."""
    return [r for r in records if r][:DEFAULT_LIMIT]


def summarize_forecast(records, key="id"):
    """Group forecast records by `key`."""
    grouped = {}
    for record in records:
        grouped.setdefault(record.get(key), []).append(record)
    return grouped


def select_forecast(record, fields=("id", "name")):
    """Is this forecast record complete enough for reporting?"""
    return all(record.get(field) not in (None, "") for field in fields)
