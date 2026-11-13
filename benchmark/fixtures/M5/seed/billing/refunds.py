"""Refund amounts per order line."""

from billing.shares import share


def refund_lines(paid_cents, lines):
    """[(name, refund cents)] for an order refunded in full. `lines` is [(name, line cents)]."""
    order_cents = sum(cents for _, cents in lines)
    return [(name, share(paid_cents, cents, order_cents)) for name, cents in lines]
