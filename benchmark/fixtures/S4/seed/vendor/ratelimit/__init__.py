"""ratelimit v1 - vendored from git@git.internal.example:platform/ratelimit.git. Do not edit by hand."""

__version__ = "1.4.0"


def allow(used, limit):
    return used < limit
