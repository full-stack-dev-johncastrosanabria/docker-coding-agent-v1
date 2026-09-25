"""The ledger the two locales are reconciled against."""


def net(charges, credits):
    """Net position in cents: everything charged, less everything credited."""
    return sum(charges) - sum(credits)


def is_balanced(charges, credits):
    """True when the books net to zero."""
    return net(charges, credits) == 0
