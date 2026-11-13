"""Late fees on overdue invoices."""

from billing.grace import last_day_without_fee
from billing.terms import due_date

LATE_FEE_CENTS = 1500


def late_fee(issued, paid_on, terms="net10"):
    """The late fee, in cents, for an invoice issued on `issued` and paid on `paid_on`."""
    return LATE_FEE_CENTS if paid_on > last_day_without_fee(due_date(issued, terms)) else 0
