"""Public holidays the back office observes. ISO dates."""

HOLIDAYS = frozenset({
    "2025-01-01", "2025-04-18", "2025-04-21", "2025-05-01", "2025-12-25", "2025-12-26",
    "2026-01-01", "2026-04-03", "2026-04-06", "2026-05-01", "2026-12-25",
})


def is_holiday(day):
    return day.isoformat() in HOLIDAYS
