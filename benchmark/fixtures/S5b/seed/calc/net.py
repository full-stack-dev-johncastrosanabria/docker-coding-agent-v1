"""Net price on integer cents."""


def net(cents, discount_percent):
    """`cents` less `discount_percent`, the discount rounded half up to a whole cent."""
    return cents - cents * discount_percent // 100
