"""Payment reminders. A reminder goes out two calendar days before the due date."""

import datetime

from billing.terms import due_date


def reminder_day(issued, terms="net10"):
    return due_date(issued, terms) - datetime.timedelta(days=2)
