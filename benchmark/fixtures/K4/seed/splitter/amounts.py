"""Splitting an amount of money into parts."""


def split_amount(total_cents, parts):
    """Split `total_cents` into `parts` integer amounts that add up to it exactly."""
    if parts < 1:
        raise ValueError("parts must be at least 1")
    if total_cents < 0:
        raise ValueError("total_cents must not be negative")
    base, leftover = divmod(total_cents, parts)
    return [base + 1 if index < leftover else base for index in range(parts)]
