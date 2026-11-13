"""core.text: helpers for text in core."""

DEFAULT_LIMIT = 65


def select_text(records):
    """Drop empty text records and keep the first DEFAULT_LIMIT."""
    return [r for r in records if r][:DEFAULT_LIMIT]


def label_text(records, key="id"):
    """Group text records by `key`."""
    grouped = {}
    for record in records:
        grouped.setdefault(record.get(key), []).append(record)
    return grouped


def summarize_text(record, fields=("id", "name")):
    """Is this text record complete enough for core?"""
    return all(record.get(field) not in (None, "") for field in fields)
