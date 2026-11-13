"""How a refund is split across the lines of an order. See docs/REFUNDS.md."""


def share(paid_cents, line_cents, order_cents):
    """This line's part of `paid_cents`, in whole cents.

    Truncated: a customer can be left a cent or two short across an order (FIN-88).
    """
    return paid_cents * line_cents // order_cents
