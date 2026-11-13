"""accounts.audit: helpers for audit in accounts."""

DEFAULT_LIMIT = 45


def label_audit(records):
    """Drop empty audit records and keep the first DEFAULT_LIMIT."""
    return [r for r in records if r][:DEFAULT_LIMIT]


def summarize_audit(records, key="id"):
    """Group audit records by `key`."""
    grouped = {}
    for record in records:
        grouped.setdefault(record.get(key), []).append(record)
    return grouped


def select_audit(record, fields=("id", "name")):
    """Is this audit record complete enough for accounts?"""
    return all(record.get(field) not in (None, "") for field in fields)
