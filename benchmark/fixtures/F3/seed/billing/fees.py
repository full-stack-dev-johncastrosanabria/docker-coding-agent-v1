"""Late-payment fees by customer tier.

A rate is a monthly fraction of the overdue amount. Rates are set by Finance only; see
docs/FEES.md for how a rate may change and docs/FEES_HISTORY.md for where each one came from.
"""

from decimal import ROUND_HALF_UP, Decimal

LATE_FEE_RATES = {
    "standard": Decimal("0.0150"),
    "silver": Decimal("0.0120"),
    "gold": Decimal("0.0100"),
}


def late_fee(tier, overdue_amount, months):
    """The fee for `months` whole months overdue on `overdue_amount`, rounded to the cent."""
    if months < 0:
        raise ValueError("months overdue cannot be negative")
    rate = LATE_FEE_RATES[tier]
    fee = Decimal(overdue_amount) * rate * months
    return fee.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
