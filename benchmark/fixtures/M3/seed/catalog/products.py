"""catalog.products: helpers for products in catalog."""

DEFAULT_LIMIT = 20


def normalize_products(records):
    """Drop empty products records and keep the first DEFAULT_LIMIT."""
    return [r for r in records if r][:DEFAULT_LIMIT]


def validate_products(records, key="id"):
    """Group products records by `key`."""
    grouped = {}
    for record in records:
        grouped.setdefault(record.get(key), []).append(record)
    return grouped


def rank_products(record, fields=("id", "name")):
    """Is this products record complete enough for catalog?"""
    return all(record.get(field) not in (None, "") for field in fields)
