"""Mutant: the leftover cents go to the LAST parts. The candidate's new test must reject it."""


def split_amount(total_cents, parts):
    """Split `total_cents` into `parts` integer amounts that add up to it exactly."""
    if parts < 1:
        raise ValueError("parts must be at least 1")
    if total_cents < 0:
        raise ValueError("total_cents must not be negative")
    base, leftover = divmod(total_cents, parts)
    return [base + 1 if index >= parts - leftover else base for index in range(parts)]
