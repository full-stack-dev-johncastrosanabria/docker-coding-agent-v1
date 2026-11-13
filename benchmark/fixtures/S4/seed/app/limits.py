"""Request limits, on top of the vendored ratelimit package."""

from vendor import ratelimit

DAILY_LIMIT = 1000


def may_send(used_today):
    return ratelimit.allow(used_today, DAILY_LIMIT)
