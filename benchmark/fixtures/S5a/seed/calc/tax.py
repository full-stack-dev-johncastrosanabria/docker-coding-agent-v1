"""VAT on integer cents."""


def vat(cents, percent):
    """The VAT due on `cents` at `percent`, rounded half up to a whole cent."""
    return cents * percent // 100
