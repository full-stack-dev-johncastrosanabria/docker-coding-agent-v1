"""The credit note a customer receives for a refund."""

from billing.shares import share


def render(paid_cents, lines):
    """One row per line and a total row. Amounts are printed as euros with two decimals."""
    order_cents = sum(cents for _, cents in lines)
    rows, total = [], 0
    for name, cents in lines:
        amount = share(paid_cents, cents, order_cents)
        total += amount
        rows.append(f"{name:<12}{amount / 100:>10.2f}")
    rows.append(f"{'REFUND':<12}{total / 100:>10.2f}")
    return "\n".join(rows)
