"""Monthly reports."""

from app.util import chunks
from app.util import legacy_parse_date as parse


def by_month(rows):
    """{(year, month): count} for {'date'} rows."""
    counts = {}
    for row in rows:
        day = parse(row["date"])
        counts[(day.year, day.month)] = counts.get((day.year, day.month), 0) + 1
    return counts


def pages(rows, size=50):
    return chunks(rows, size)
