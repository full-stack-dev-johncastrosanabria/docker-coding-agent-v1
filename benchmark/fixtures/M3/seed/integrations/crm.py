"""integrations.crm: helpers for crm in integrations."""

DEFAULT_LIMIT = 25


def summarize_crm(records):
    """Drop empty crm records and keep the first DEFAULT_LIMIT."""
    return [r for r in records if r][:DEFAULT_LIMIT]


def select_crm(records, key="id"):
    """Group crm records by `key`."""
    grouped = {}
    for record in records:
        grouped.setdefault(record.get(key), []).append(record)
    return grouped


def label_crm(record, fields=("id", "name")):
    """Is this crm record complete enough for integrations?"""
    return all(record.get(field) not in (None, "") for field in fields)
