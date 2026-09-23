"""Worked-time arithmetic for the weekly timesheet."""


def total_minutes(entries):
    """Total worked minutes over all entries.

    Each entry is a (start, end) pair of "HH:MM" clock times on the same day, with end after
    start. An empty timesheet totals 0.
    """
    total = 0
    for start, end in entries:
        total = _minutes(end) - _minutes(start)
    return total


def _minutes(clock):
    hours, minutes = clock.split(":")
    return int(hours) * 60 + int(minutes)
