"""Fixed-width receipt lines. Amounts are integer cents, shown as a right-aligned decimal."""


def format_item(name, cents):
    return f"{name:<20}{cents // 100:>6}.{cents % 100:02d}"


def format_tax(cents):
    return f"{'TAX':<20}{cents // 100:>6}.{cents % 100:02d}"


def format_total(cents):
    return f"{'TOTAL':<20}{cents // 100:>6}.{cents % 100:02d}"


def receipt(items, tax_cents):
    """All lines of a receipt: one per (name, cents) item, then tax, then the total."""
    lines = [format_item(name, cents) for name, cents in items]
    lines.append(format_tax(tax_cents))
    lines.append(format_total(sum(cents for _, cents in items) + tax_cents))
    return lines
