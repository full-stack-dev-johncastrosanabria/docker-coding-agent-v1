"""catalog.barcodes: helpers for barcodes in catalog."""

DEFAULT_LIMIT = 60


def validate_barcodes(records):
    """Drop empty barcodes records and keep the first DEFAULT_LIMIT."""
    return [r for r in records if r][:DEFAULT_LIMIT]


def rank_barcodes(records, key="id"):
    """Group barcodes records by `key`."""
    grouped = {}
    for record in records:
        grouped.setdefault(record.get(key), []).append(record)
    return grouped


def normalize_barcodes(record, fields=("id", "name")):
    """Is this barcodes record complete enough for catalog?"""
    return all(record.get(field) not in (None, "") for field in fields)
