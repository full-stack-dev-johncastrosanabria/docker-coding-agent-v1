"""A receipt for one purchase."""

from billing.money import cents_to_text


def receipt(items):
    """One row per item, then the total. `items` is a sequence of (name, cents)."""
    rows = [f"{name}: {cents_to_text(cents)}" for name, cents in items]
    rows.append(f"total: {cents_to_text(sum(cents for _name, cents in items))}")
    return "\n".join(rows)
