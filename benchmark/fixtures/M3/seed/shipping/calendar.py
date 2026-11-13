"""Carrier pickup days. Carriers keep their OWN calendar: they collect on Saturdays and ignore
our public holidays, so this deliberately does not use core.timeutil.workdays."""

import datetime


def next_pickup_day(day):
    """The first day on or after `day` a carrier collects: any day but Sunday."""
    while day.weekday() == 6:
        day += datetime.timedelta(days=1)
    return day
