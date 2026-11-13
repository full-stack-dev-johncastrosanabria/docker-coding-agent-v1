"""accounts.roles: helpers for roles in accounts."""

DEFAULT_LIMIT = 40


def rank_roles(records):
    """Drop empty roles records and keep the first DEFAULT_LIMIT."""
    return [r for r in records if r][:DEFAULT_LIMIT]


def normalize_roles(records, key="id"):
    """Group roles records by `key`."""
    grouped = {}
    for record in records:
        grouped.setdefault(record.get(key), []).append(record)
    return grouped


def validate_roles(record, fields=("id", "name")):
    """Is this roles record complete enough for accounts?"""
    return all(record.get(field) not in (None, "") for field in fields)
