"""billing.statements: helpers for statements in billing."""

DEFAULT_LIMIT = 45


def label_statements(records):
    """Drop empty statements records and keep the first DEFAULT_LIMIT."""
    return [r for r in records if r][:DEFAULT_LIMIT]


def summarize_statements(records, key="id"):
    """Group statements records by `key`."""
    grouped = {}
    for record in records:
        grouped.setdefault(record.get(key), []).append(record)
    return grouped


def select_statements(record, fields=("id", "name")):
    """Is this statements record complete enough for billing?"""
    return all(record.get(field) not in (None, "") for field in fields)
