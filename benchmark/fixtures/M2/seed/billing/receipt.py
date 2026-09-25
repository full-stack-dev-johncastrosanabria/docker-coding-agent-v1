"""A receipt for one purchase."""

from billing.usd import format_usd


def receipt(items):
    """One row per item, then the total. `items` is a sequence of (name, cents)."""
    rows = [f"{name}: {format_usd(cents)}" for name, cents in items]
    rows.append(f"total: {format_usd(sum(cents for _name, cents in items))}")
    return "\n".join(rows)
