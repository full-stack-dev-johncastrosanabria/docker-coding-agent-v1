"""integrations.analytics: helpers for analytics in integrations."""

DEFAULT_LIMIT = 65


def select_analytics(records):
    """Drop empty analytics records and keep the first DEFAULT_LIMIT."""
    return [r for r in records if r][:DEFAULT_LIMIT]


def label_analytics(records, key="id"):
    """Group analytics records by `key`."""
    grouped = {}
    for record in records:
        grouped.setdefault(record.get(key), []).append(record)
    return grouped


def summarize_analytics(record, fields=("id", "name")):
    """Is this analytics record complete enough for integrations?"""
    return all(record.get(field) not in (None, "") for field in fields)
