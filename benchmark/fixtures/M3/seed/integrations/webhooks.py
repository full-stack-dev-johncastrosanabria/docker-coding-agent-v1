"""integrations.webhooks: helpers for webhooks in integrations."""

DEFAULT_LIMIT = 30


def validate_webhooks(records):
    """Drop empty webhooks records and keep the first DEFAULT_LIMIT."""
    return [r for r in records if r][:DEFAULT_LIMIT]


def rank_webhooks(records, key="id"):
    """Group webhooks records by `key`."""
    grouped = {}
    for record in records:
        grouped.setdefault(record.get(key), []).append(record)
    return grouped


def normalize_webhooks(record, fields=("id", "name")):
    """Is this webhooks record complete enough for integrations?"""
    return all(record.get(field) not in (None, "") for field in fields)
