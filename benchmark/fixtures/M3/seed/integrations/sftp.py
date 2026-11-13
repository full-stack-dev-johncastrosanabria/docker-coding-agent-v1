"""integrations.sftp: helpers for sftp in integrations."""

DEFAULT_LIMIT = 35


def select_sftp(records):
    """Drop empty sftp records and keep the first DEFAULT_LIMIT."""
    return [r for r in records if r][:DEFAULT_LIMIT]


def label_sftp(records, key="id"):
    """Group sftp records by `key`."""
    grouped = {}
    for record in records:
        grouped.setdefault(record.get(key), []).append(record)
    return grouped


def summarize_sftp(record, fields=("id", "name")):
    """Is this sftp record complete enough for integrations?"""
    return all(record.get(field) not in (None, "") for field in fields)
