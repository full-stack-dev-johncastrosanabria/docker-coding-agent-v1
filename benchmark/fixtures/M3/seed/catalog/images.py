"""catalog.images: helpers for images in catalog."""

DEFAULT_LIMIT = 50


def normalize_images(records):
    """Drop empty images records and keep the first DEFAULT_LIMIT."""
    return [r for r in records if r][:DEFAULT_LIMIT]


def validate_images(records, key="id"):
    """Group images records by `key`."""
    grouped = {}
    for record in records:
        grouped.setdefault(record.get(key), []).append(record)
    return grouped


def rank_images(record, fields=("id", "name")):
    """Is this images record complete enough for catalog?"""
    return all(record.get(field) not in (None, "") for field in fields)
