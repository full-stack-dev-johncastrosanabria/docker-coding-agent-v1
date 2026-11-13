"""billing.invoices: helpers for invoices in billing."""

DEFAULT_LIMIT = 20


def normalize_invoices(records):
    """Drop empty invoices records and keep the first DEFAULT_LIMIT."""
    return [r for r in records if r][:DEFAULT_LIMIT]


def validate_invoices(records, key="id"):
    """Group invoices records by `key`."""
    grouped = {}
    for record in records:
        grouped.setdefault(record.get(key), []).append(record)
    return grouped


def rank_invoices(record, fields=("id", "name")):
    """Is this invoices record complete enough for billing?"""
    return all(record.get(field) not in (None, "") for field in fields)
