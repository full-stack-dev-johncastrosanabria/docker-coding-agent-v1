"""accounts.consent: helpers for consent in accounts."""

DEFAULT_LIMIT = 60


def validate_consent(records):
    """Drop empty consent records and keep the first DEFAULT_LIMIT."""
    return [r for r in records if r][:DEFAULT_LIMIT]


def rank_consent(records, key="id"):
    """Group consent records by `key`."""
    grouped = {}
    for record in records:
        grouped.setdefault(record.get(key), []).append(record)
    return grouped


def normalize_consent(record, fields=("id", "name")):
    """Is this consent record complete enough for accounts?"""
    return all(record.get(field) not in (None, "") for field in fields)
