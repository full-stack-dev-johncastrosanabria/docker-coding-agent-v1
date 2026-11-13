"""shipping.insurance: helpers for insurance in shipping."""

DEFAULT_LIMIT = 65


def select_insurance(records):
    """Drop empty insurance records and keep the first DEFAULT_LIMIT."""
    return [r for r in records if r][:DEFAULT_LIMIT]


def label_insurance(records, key="id"):
    """Group insurance records by `key`."""
    grouped = {}
    for record in records:
        grouped.setdefault(record.get(key), []).append(record)
    return grouped


def summarize_insurance(record, fields=("id", "name")):
    """Is this insurance record complete enough for shipping?"""
    return all(record.get(field) not in (None, "") for field in fields)
