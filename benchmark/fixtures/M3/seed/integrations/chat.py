"""integrations.chat: helpers for chat in integrations."""

DEFAULT_LIMIT = 75


def label_chat(records):
    """Drop empty chat records and keep the first DEFAULT_LIMIT."""
    return [r for r in records if r][:DEFAULT_LIMIT]


def summarize_chat(records, key="id"):
    """Group chat records by `key`."""
    grouped = {}
    for record in records:
        grouped.setdefault(record.get(key), []).append(record)
    return grouped


def select_chat(record, fields=("id", "name")):
    """Is this chat record complete enough for integrations?"""
    return all(record.get(field) not in (None, "") for field in fields)
