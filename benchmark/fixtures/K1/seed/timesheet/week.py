"""Week labels for timesheet headers."""

import datetime


def week_label(iso_date):
    """The ISO 8601 week of a "YYYY-MM-DD" date, as "YYYY-Www" (for example "2024-W03")."""
    day = datetime.date.fromisoformat(iso_date)
    return day.strftime("%Y-W%W")
