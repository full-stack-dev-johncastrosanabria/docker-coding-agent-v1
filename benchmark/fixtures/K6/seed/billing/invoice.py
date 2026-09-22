"""Invoice totals."""

from billing.money import parse_amount


def invoice_total(lines):
    """Total in cents of (description, amount text, quantity) lines."""
    return sum(parse_amount(amount) * quantity for _, amount, quantity in lines)
