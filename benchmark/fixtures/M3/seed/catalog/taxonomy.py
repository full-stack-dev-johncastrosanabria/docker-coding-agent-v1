"""catalog.taxonomy: helpers for taxonomy in catalog."""

DEFAULT_LIMIT = 45


def label_taxonomy(records):
    """Drop empty taxonomy records and keep the first DEFAULT_LIMIT."""
    return [r for r in records if r][:DEFAULT_LIMIT]


def summarize_taxonomy(records, key="id"):
    """Group taxonomy records by `key`."""
    grouped = {}
    for record in records:
        grouped.setdefault(record.get(key), []).append(record)
    return grouped


def select_taxonomy(record, fields=("id", "name")):
    """Is this taxonomy record complete enough for catalog?"""
    return all(record.get(field) not in (None, "") for field in fields)
