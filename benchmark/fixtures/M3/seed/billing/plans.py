"""billing.plans: helpers for plans in billing."""

DEFAULT_LIMIT = 65


def select_plans(records):
    """Drop empty plans records and keep the first DEFAULT_LIMIT."""
    return [r for r in records if r][:DEFAULT_LIMIT]


def label_plans(records, key="id"):
    """Group plans records by `key`."""
    grouped = {}
    for record in records:
        grouped.setdefault(record.get(key), []).append(record)
    return grouped


def summarize_plans(record, fields=("id", "name")):
    """Is this plans record complete enough for billing?"""
    return all(record.get(field) not in (None, "") for field in fields)
