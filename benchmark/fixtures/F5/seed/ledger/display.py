"""How an amount is printed on a receipt line."""

from ledger.limits import MAX_CENTS, MIN_CENTS


def format_amount(cents):
    """123450 -> '1234.50'. Every amount the ledger accepts."""
    if not MIN_CENTS <= cents <= MAX_CENTS:
        raise ValueError(f"{cents} cents is outside the accepted range")
    return f"{cents // 100}.{cents % 100:02d}"
