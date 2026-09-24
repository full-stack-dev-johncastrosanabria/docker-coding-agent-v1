"""Line rules. Each takes the file's lines and returns a Finding for every offending line.

Line numbers start at 1.
"""

from checks.registry import Finding, rule


@rule("L001")
def trailing_whitespace(lines):
    return [Finding("L001", number, "trailing whitespace")
            for number, line in enumerate(lines, 1) if line != line.rstrip()]


@rule("L002")
def tab_indentation(lines):
    return [Finding("L002", number, "tab used for indentation")
            for number, line in enumerate(lines, 1) if line.startswith("\t")]
