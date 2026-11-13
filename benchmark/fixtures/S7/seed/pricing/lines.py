"""Line-item arithmetic. Every amount is a whole number of cents."""


def line_total(quantity, unit_price):
    """The total for one invoice line.

    `quantity` and `unit_price` are non-negative integers.
    """
    if quantity < 0:
        raise ValueError("quantity must not be negative")
    if unit_price < 0:
        raise ValueError("unit_price must not be negative")
    return quantity * unit_price
