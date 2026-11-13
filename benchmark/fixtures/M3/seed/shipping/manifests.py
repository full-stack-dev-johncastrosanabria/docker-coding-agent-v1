"""shipping.manifests: helpers for manifests in shipping."""

DEFAULT_LIMIT = 75


def label_manifests(records):
    """Drop empty manifests records and keep the first DEFAULT_LIMIT."""
    return [r for r in records if r][:DEFAULT_LIMIT]


def summarize_manifests(records, key="id"):
    """Group manifests records by `key`."""
    grouped = {}
    for record in records:
        grouped.setdefault(record.get(key), []).append(record)
    return grouped


def select_manifests(record, fields=("id", "name")):
    """Is this manifests record complete enough for shipping?"""
    return all(record.get(field) not in (None, "") for field in fields)
