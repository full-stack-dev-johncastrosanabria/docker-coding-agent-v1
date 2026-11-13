"""Calendar periods: months and quarters."""

import datetime


def month_start(day):
    return day.replace(day=1)


def month_end(day):
    following = (day.replace(day=28) + datetime.timedelta(days=4)).replace(day=1)
    return following - datetime.timedelta(days=1)


def quarter_of(day):
    return (day.month - 1) // 3 + 1
