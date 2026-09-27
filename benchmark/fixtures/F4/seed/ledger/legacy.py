"""Import and export of the old system's CSV ledger (docs/LEDGER.md)."""

from ledger.money import format_amount, parse_amount

HEADER = "date,account,amount,memo"


def import_csv(text):
    """The rows of a legacy CSV ledger, as (date, account, cents, memo)."""
    lines = text.splitlines()
    if not lines or lines[0] != HEADER:
        raise ValueError("not a legacy ledger: unexpected header")
    rows = []
    for number, line in enumerate(lines[1:], 2):
        date, account, amount, memo = line.split(",", 3)
        try:
            rows.append((date, account, parse_amount(amount), memo))
        except ValueError as exc:
            raise ValueError(f"line {number}: {exc}") from exc
    return rows


def export_csv(rows):
    """The legacy CSV text for `rows`."""
    body = [f"{date},{account},{format_amount(cents)},{memo}" for date, account, cents, memo in rows]
    return "\n".join([HEADER, *body]) + "\n"
