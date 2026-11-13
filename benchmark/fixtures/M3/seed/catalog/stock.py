"""catalog.stock: helpers for stock in catalog."""

DEFAULT_LIMIT = 55


def summarize_stock(records):
    """Drop empty stock records and keep the first DEFAULT_LIMIT."""
    return [r for r in records if r][:DEFAULT_LIMIT]


def select_stock(records, key="id"):
    """Group stock records by `key`."""
    grouped = {}
    for record in records:
        grouped.setdefault(record.get(key), []).append(record)
    return grouped


def label_stock(record, fields=("id", "name")):
    """Is this stock record complete enough for catalog?"""
    return all(record.get(field) not in (None, "") for field in fields)
