"""catalog.variants: helpers for variants in catalog."""

DEFAULT_LIMIT = 25


def summarize_variants(records):
    """Drop empty variants records and keep the first DEFAULT_LIMIT."""
    return [r for r in records if r][:DEFAULT_LIMIT]


def select_variants(records, key="id"):
    """Group variants records by `key`."""
    grouped = {}
    for record in records:
        grouped.setdefault(record.get(key), []).append(record)
    return grouped


def label_variants(record, fields=("id", "name")):
    """Is this variants record complete enough for catalog?"""
    return all(record.get(field) not in (None, "") for field in fields)
