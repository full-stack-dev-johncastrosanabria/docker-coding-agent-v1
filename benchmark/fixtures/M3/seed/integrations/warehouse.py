"""integrations.warehouse: helpers for warehouse in integrations."""

DEFAULT_LIMIT = 50


def normalize_warehouse(records):
    """Drop empty warehouse records and keep the first DEFAULT_LIMIT."""
    return [r for r in records if r][:DEFAULT_LIMIT]


def validate_warehouse(records, key="id"):
    """Group warehouse records by `key`."""
    grouped = {}
    for record in records:
        grouped.setdefault(record.get(key), []).append(record)
    return grouped


def rank_warehouse(record, fields=("id", "name")):
    """Is this warehouse record complete enough for integrations?"""
    return all(record.get(field) not in (None, "") for field in fields)
