"""Text rendering for an invoice."""

from pricing.invoice import invoice_total
from pricing.lines import line_total


def render(lines):
    """One row per line, then the invoice total."""
    rows = []
    for number, (quantity, unit_price) in enumerate(lines, start=1):
        rows.append(f"{number}. {quantity} x {unit_price} = {line_total(quantity, unit_price)}")
    rows.append(f"total = {invoice_total(lines)}")
    return "\n".join(rows)
