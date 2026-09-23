"""CSV export."""

from app import util


def export_rows(rows):
    """'date,amount' lines, oldest first, dates normalized to ISO."""
    ordered = sorted(rows, key=lambda row: util.legacy_parse_date(row["date"]))
    return [f"{util.legacy_parse_date(row['date']).isoformat()},{row['amount']}" for row in ordered]
