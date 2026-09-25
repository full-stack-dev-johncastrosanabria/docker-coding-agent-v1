"""The front desk."""

from library.loans import renew


def handle_renewal(loan):
    """Renew a loan and report the outcome to the borrower."""
    renew(loan)
    return f"{loan.item} renewed, now {loan.weeks} weeks"
