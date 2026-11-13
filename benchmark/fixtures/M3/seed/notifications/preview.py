"""notifications.preview: helpers for preview in notifications."""

DEFAULT_LIMIT = 75


def label_preview(records):
    """Drop empty preview records and keep the first DEFAULT_LIMIT."""
    return [r for r in records if r][:DEFAULT_LIMIT]


def summarize_preview(records, key="id"):
    """Group preview records by `key`."""
    grouped = {}
    for record in records:
        grouped.setdefault(record.get(key), []).append(record)
    return grouped


def select_preview(record, fields=("id", "name")):
    """Is this preview record complete enough for notifications?"""
    return all(record.get(field) not in (None, "") for field in fields)
