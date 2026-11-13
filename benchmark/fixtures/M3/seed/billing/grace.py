"""The grace period after a due date, before a late fee applies."""

from core.timeutil.workdays import next_business_day

import datetime


def last_day_without_fee(due):
    """Customers may still pay without a fee on the first business day on or after the day
    following the due date."""
    return next_business_day(due + datetime.timedelta(days=1))
