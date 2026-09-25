"""Loans and their renewals."""


class Loan:
    """A borrowed item."""

    def __init__(self, item, weeks=2):
        self.item = item
        self.weeks = weeks
        self.renewals = 0


def renew(loan, weeks=2):
    """Extend a loan by `weeks` and count the renewal. Returns the loan."""
    loan.weeks += weeks
    loan.renewals += 1
    return loan
