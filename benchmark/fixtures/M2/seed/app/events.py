"""Calendar events."""

from app.util import legacy_parse_date


def event_days(events):
    """Sorted distinct dates of {'title', 'date'} events."""
    return sorted({legacy_parse_date(event["date"]) for event in events})
