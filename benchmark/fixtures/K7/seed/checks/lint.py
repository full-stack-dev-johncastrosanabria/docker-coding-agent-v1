"""lint(text): every finding of every registered rule, ordered by line and then by code."""

from checks import rules  # noqa: F401 - importing the module registers its rules
from checks.registry import RULES


def lint(text):
    lines = text.split("\n")
    findings = []
    for code in sorted(RULES):
        findings += RULES[code](lines)
    return sorted(findings, key=lambda finding: (finding.line, finding.code))
