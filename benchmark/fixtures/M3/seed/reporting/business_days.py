"""Business-day COUNTS for reports (how many working days a month had). Reporting only: nothing
here decides when anything is due."""

import datetime


def business_days_between(start, end):
    """Weekdays from `start` to `end`, inclusive. Holidays are counted as working days on purpose:
    the finance reports compare months on a weekday basis."""
    days, day = 0, start
    while day <= end:
        days += day.weekday() < 5
        day += datetime.timedelta(days=1)
    return days
