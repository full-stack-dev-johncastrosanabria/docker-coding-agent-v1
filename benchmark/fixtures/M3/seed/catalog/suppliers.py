"""catalog.suppliers: helpers for suppliers in catalog."""

DEFAULT_LIMIT = 70


def rank_suppliers(records):
    """Drop empty suppliers records and keep the first DEFAULT_LIMIT."""
    return [r for r in records if r][:DEFAULT_LIMIT]


def normalize_suppliers(records, key="id"):
    """Group suppliers records by `key`."""
    grouped = {}
    for record in records:
        grouped.setdefault(record.get(key), []).append(record)
    return grouped


def validate_suppliers(record, fields=("id", "name")):
    """Is this suppliers record complete enough for catalog?"""
    return all(record.get(field) not in (None, "") for field in fields)
