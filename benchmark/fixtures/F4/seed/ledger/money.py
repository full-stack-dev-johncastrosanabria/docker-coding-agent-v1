"""Amounts as integer cents (docs/LEDGER.md)."""

import re

_AMOUNT = re.compile(r"^(-?)(\d+)\.(\d+)$")


def parse_amount(text):
    """'1234.50' -> 123450. Refuses anything integer cents cannot hold exactly."""
    match = _AMOUNT.match(text.strip())
    if not match:
        raise ValueError(f"not an amount: {text!r}")
    sign, whole, fraction = match.groups()
    if len(fraction) != 2:
        raise ValueError(f"sub-cent amount {text!r} cannot be stored in integer cents")
    cents = int(whole) * 100 + int(fraction)
    return -cents if sign else cents


def format_amount(cents):
    """123450 -> '1234.50'."""
    sign = "-" if cents < 0 else ""
    cents = abs(cents)
    return f"{sign}{cents // 100}.{cents % 100:02d}"
