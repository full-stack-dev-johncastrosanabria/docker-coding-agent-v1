"""accounts.referrals: helpers for referrals in accounts."""

DEFAULT_LIMIT = 75


def label_referrals(records):
    """Drop empty referrals records and keep the first DEFAULT_LIMIT."""
    return [r for r in records if r][:DEFAULT_LIMIT]


def summarize_referrals(records, key="id"):
    """Group referrals records by `key`."""
    grouped = {}
    for record in records:
        grouped.setdefault(record.get(key), []).append(record)
    return grouped


def select_referrals(record, fields=("id", "name")):
    """Is this referrals record complete enough for accounts?"""
    return all(record.get(field) not in (None, "") for field in fields)
