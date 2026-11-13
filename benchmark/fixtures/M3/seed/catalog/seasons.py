"""catalog.seasons: helpers for seasons in catalog."""

DEFAULT_LIMIT = 75


def label_seasons(records):
    """Drop empty seasons records and keep the first DEFAULT_LIMIT."""
    return [r for r in records if r][:DEFAULT_LIMIT]


def summarize_seasons(records, key="id"):
    """Group seasons records by `key`."""
    grouped = {}
    for record in records:
        grouped.setdefault(record.get(key), []).append(record)
    return grouped


def select_seasons(record, fields=("id", "name")):
    """Is this seasons record complete enough for catalog?"""
    return all(record.get(field) not in (None, "") for field in fields)
