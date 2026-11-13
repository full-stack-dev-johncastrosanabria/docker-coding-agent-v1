"""notifications.sms: helpers for sms in notifications."""

DEFAULT_LIMIT = 25


def summarize_sms(records):
    """Drop empty sms records and keep the first DEFAULT_LIMIT."""
    return [r for r in records if r][:DEFAULT_LIMIT]


def select_sms(records, key="id"):
    """Group sms records by `key`."""
    grouped = {}
    for record in records:
        grouped.setdefault(record.get(key), []).append(record)
    return grouped


def label_sms(record, fields=("id", "name")):
    """Is this sms record complete enough for notifications?"""
    return all(record.get(field) not in (None, "") for field in fields)
