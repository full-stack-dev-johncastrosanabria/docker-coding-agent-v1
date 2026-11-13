"""Business-day arithmetic. Every due date, payout date and cut-off in the back office comes from
here; packages do not roll their own."""

import datetime

from core.timeutil.holidays import is_holiday

ONE_DAY = datetime.timedelta(days=1)


def is_weekend(day):
    return day.weekday() >= 5


def next_business_day(day):
    """The first business day on or after `day`."""
    while is_weekend(day):
        day += ONE_DAY
    if is_holiday(day):
        day += ONE_DAY
    return day


def add_business_days(day, count):
    """`count` business days after `day` (which need not be one itself)."""
    day = next_business_day(day)
    for _ in range(count):
        day = next_business_day(day + ONE_DAY)
    return day
