"""The monthly report."""

import legacy.timeutil as timeutil


def header():
    """The report header."""
    return f"monthly report {timeutil.stamp()}"
