"""integrations.tax_service: helpers for tax service in integrations."""

DEFAULT_LIMIT = 40


def rank_tax_service(records):
    """Drop empty tax service records and keep the first DEFAULT_LIMIT."""
    return [r for r in records if r][:DEFAULT_LIMIT]


def normalize_tax_service(records, key="id"):
    """Group tax service records by `key`."""
    grouped = {}
    for record in records:
        grouped.setdefault(record.get(key), []).append(record)
    return grouped


def validate_tax_service(record, fields=("id", "name")):
    """Is this tax service record complete enough for integrations?"""
    return all(record.get(field) not in (None, "") for field in fields)
