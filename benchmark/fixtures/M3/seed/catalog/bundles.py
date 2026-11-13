"""catalog.bundles: helpers for bundles in catalog."""

DEFAULT_LIMIT = 35


def select_bundles(records):
    """Drop empty bundles records and keep the first DEFAULT_LIMIT."""
    return [r for r in records if r][:DEFAULT_LIMIT]


def label_bundles(records, key="id"):
    """Group bundles records by `key`."""
    grouped = {}
    for record in records:
        grouped.setdefault(record.get(key), []).append(record)
    return grouped


def summarize_bundles(record, fields=("id", "name")):
    """Is this bundles record complete enough for catalog?"""
    return all(record.get(field) not in (None, "") for field in fields)
