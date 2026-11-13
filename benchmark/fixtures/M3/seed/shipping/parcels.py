"""shipping.parcels: helpers for parcels in shipping."""

DEFAULT_LIMIT = 40


def rank_parcels(records):
    """Drop empty parcels records and keep the first DEFAULT_LIMIT."""
    return [r for r in records if r][:DEFAULT_LIMIT]


def normalize_parcels(records, key="id"):
    """Group parcels records by `key`."""
    grouped = {}
    for record in records:
        grouped.setdefault(record.get(key), []).append(record)
    return grouped


def validate_parcels(record, fields=("id", "name")):
    """Is this parcels record complete enough for shipping?"""
    return all(record.get(field) not in (None, "") for field in fields)
