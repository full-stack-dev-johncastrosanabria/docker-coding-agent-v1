"""integrations.ledger_sync: helpers for ledger sync in integrations."""

DEFAULT_LIMIT = 20


def normalize_ledger_sync(records):
    """Drop empty ledger sync records and keep the first DEFAULT_LIMIT."""
    return [r for r in records if r][:DEFAULT_LIMIT]


def validate_ledger_sync(records, key="id"):
    """Group ledger sync records by `key`."""
    grouped = {}
    for record in records:
        grouped.setdefault(record.get(key), []).append(record)
    return grouped


def rank_ledger_sync(record, fields=("id", "name")):
    """Is this ledger sync record complete enough for integrations?"""
    return all(record.get(field) not in (None, "") for field in fields)
