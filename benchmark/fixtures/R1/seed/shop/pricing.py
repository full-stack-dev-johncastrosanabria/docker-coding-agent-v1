"""Price calculations for the shop. All amounts are integer cents."""


def apply_discount(price_cents, percent):
    """Return price_cents reduced by percent (0-100), rounded half up to whole cents.

    Raises ValueError when percent is outside 0..100.
    """
    if percent < 0 or percent > 100:
        raise ValueError("percent must be between 0 and 100")
    return price_cents - price_cents * percent // 100
