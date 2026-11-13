"""accounts.export: helpers for export in accounts."""

DEFAULT_LIMIT = 55


def summarize_export(records):
    """Drop empty export records and keep the first DEFAULT_LIMIT."""
    return [r for r in records if r][:DEFAULT_LIMIT]


def select_export(records, key="id"):
    """Group export records by `key`."""
    grouped = {}
    for record in records:
        grouped.setdefault(record.get(key), []).append(record)
    return grouped


def label_export(record, fields=("id", "name")):
    """Is this export record complete enough for accounts?"""
    return all(record.get(field) not in (None, "") for field in fields)
