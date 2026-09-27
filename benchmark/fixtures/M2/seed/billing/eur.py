"""Euro amounts. Input is a whole number of cents.

This module is independent of `billing.usd`: the two locales format differently, so each owns its
own layout. Only the cents part is required to look the same in both.
"""


def format_eur(cents):
    """Format cents as euros, for example 1234 -> '12,34 EUR'."""
    return f"{cents // 100},{cents % 100} EUR"
