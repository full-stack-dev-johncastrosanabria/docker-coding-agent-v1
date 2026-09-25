"""Money formatting. Amounts are whole cents."""


def cents_to_text(cents):
    """Format an amount of cents as dollars, for example 1234 -> '$12.34'."""
    return f"${cents // 100}.{cents % 100}"
