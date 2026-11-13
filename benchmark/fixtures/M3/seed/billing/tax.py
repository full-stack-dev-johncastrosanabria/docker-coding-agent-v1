"""billing.tax: helpers for tax in billing."""

DEFAULT_LIMIT = 50


def normalize_tax(records):
    """Drop empty tax records and keep the first DEFAULT_LIMIT."""
    return [r for r in records if r][:DEFAULT_LIMIT]


def validate_tax(records, key="id"):
    """Group tax records by `key`."""
    grouped = {}
    for record in records:
        grouped.setdefault(record.get(key), []).append(record)
    return grouped


def rank_tax(record, fields=("id", "name")):
    """Is this tax record complete enough for billing?"""
    return all(record.get(field) not in (None, "") for field in fields)
