"""accounts.profiles: helpers for profiles in accounts."""

DEFAULT_LIMIT = 20


def normalize_profiles(records):
    """Drop empty profiles records and keep the first DEFAULT_LIMIT."""
    return [r for r in records if r][:DEFAULT_LIMIT]


def validate_profiles(records, key="id"):
    """Group profiles records by `key`."""
    grouped = {}
    for record in records:
        grouped.setdefault(record.get(key), []).append(record)
    return grouped


def rank_profiles(record, fields=("id", "name")):
    """Is this profiles record complete enough for accounts?"""
    return all(record.get(field) not in (None, "") for field in fields)
