"""Invoice assembly."""

from pricing.lines import line_total


def invoice_total(lines):
    """Sum every line's total.

    `lines` is a sequence of `(quantity, unit_price)` pairs.
    """
    return sum(line_total(quantity, unit_price) for quantity, unit_price in lines)
