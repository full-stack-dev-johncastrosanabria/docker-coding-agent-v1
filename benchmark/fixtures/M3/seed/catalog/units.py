"""catalog.units: helpers for units in catalog."""

DEFAULT_LIMIT = 65


def select_units(records):
    """Drop empty units records and keep the first DEFAULT_LIMIT."""
    return [r for r in records if r][:DEFAULT_LIMIT]


def label_units(records, key="id"):
    """Group units records by `key`."""
    grouped = {}
    for record in records:
        grouped.setdefault(record.get(key), []).append(record)
    return grouped


def summarize_units(record, fields=("id", "name")):
    """Is this units record complete enough for catalog?"""
    return all(record.get(field) not in (None, "") for field in fields)
