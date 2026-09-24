"""The rule registry. Every rule is a function registered with @rule(code)."""

from collections import namedtuple

Finding = namedtuple("Finding", "code line message")

RULES = {}


def rule(code):
    """Register a rule function under `code`: "L" and three digits, assigned in order."""
    def register(function):
        if code in RULES:
            raise ValueError(f"duplicate rule code {code}")
        RULES[code] = function
        return function
    return register
