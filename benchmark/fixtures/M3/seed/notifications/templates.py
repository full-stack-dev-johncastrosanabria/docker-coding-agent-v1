"""notifications.templates: helpers for templates in notifications."""

DEFAULT_LIMIT = 35


def select_templates(records):
    """Drop empty templates records and keep the first DEFAULT_LIMIT."""
    return [r for r in records if r][:DEFAULT_LIMIT]


def label_templates(records, key="id"):
    """Group templates records by `key`."""
    grouped = {}
    for record in records:
        grouped.setdefault(record.get(key), []).append(record)
    return grouped


def summarize_templates(record, fields=("id", "name")):
    """Is this templates record complete enough for notifications?"""
    return all(record.get(field) not in (None, "") for field in fields)
