"""US dollar amounts. Input is a whole number of cents."""


def format_usd(cents):
    """Format cents as dollars, for example 1234 -> '$12.34'."""
    return f"${cents // 100}.{cents % 100}"
