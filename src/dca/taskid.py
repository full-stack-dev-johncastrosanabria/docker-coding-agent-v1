"""Task fingerprint (tasks.md T034, data-model.md "Task fingerprint algorithm").

Standard library only. One helper, used for every run AND for every approval check, because the
fingerprint is how the launcher decides whether an approval granted for one run may be reused by
another. Two implementations would eventually disagree, and the disagreement would either carry a
grant onto work the developer never approved or reject a legitimate re-run.

THE NORMALIZATION IS DELIBERATELY MINIMAL. Only CRLF becomes LF. No trimming, no whitespace
collapsing, no Unicode normalization, no case folding - because each of those would make two
materially different prompts share a fingerprint, and a grant is scoped by fingerprint. The
published test vector in data-model.md pins this, and tests/unit/test_taskid.py holds it there.
"""

import hashlib
import json

PREFIX = "sha256:"


class UsageError(ValueError):
    """Bad input from the caller. The CLI maps this to exit 2."""

    exit_code = 2


def _text(value, what):
    """Decode one input to str. Invalid UTF-8 refuses the request rather than substituting."""
    if isinstance(value, str):
        return value
    if isinstance(value, (bytes, bytearray)):
        try:
            return bytes(value).decode("utf-8")
        except UnicodeDecodeError as exc:
            raise UsageError(f"{what} is not valid UTF-8") from exc
    raise UsageError(f"{what} must be text, got {type(value).__name__}")


def _normalize(value, what):
    """Step 2: CRLF -> LF, and nothing else."""
    return _text(value, what).replace("\r\n", "\n")


def _lines(value, what):
    """Criteria: a list of lines, or one blob split on newline AFTER normalization.

    Lines of length 0 are omitted; no other line is changed or dropped, so a whitespace-only line
    survives and keeps its meaning.
    """
    if value is None:
        return []
    if isinstance(value, (str, bytes, bytearray)):
        parts = _normalize(value, what).split("\n")
    else:
        try:
            parts = [_normalize(item, what) for item in value]
        except TypeError as exc:
            raise UsageError(f"{what} must be a list of strings or a single blob") from exc
    return [line for line in parts if len(line) > 0]


def _commands(value, what):
    """Verification commands, in command-line order. Order is preserved and is significant."""
    if value is None:
        return []
    if isinstance(value, (str, bytes, bytearray)):
        raise UsageError(f"{what} must be a list, not a single string")
    try:
        return [_normalize(item, what) for item in value]
    except TypeError as exc:
        raise UsageError(f"{what} must be a list of strings") from exc


def canonical_object(prompt, acceptance_criteria=None, verification_commands=None):
    """Step 3: exactly three keys, with array element order preserved."""
    return {
        "prompt": _normalize(prompt, "prompt"),
        "acceptance_criteria": _lines(acceptance_criteria, "acceptance_criteria"),
        "verification_commands": _commands(verification_commands, "verification_commands"),
    }


def canonical_bytes(prompt, acceptance_criteria=None, verification_commands=None):
    """Step 4: sorted keys, no separator spaces, literal non-ASCII, UTF-8 encoded."""
    obj = canonical_object(prompt, acceptance_criteria, verification_commands)
    return json.dumps(obj, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":")).encode("utf-8")


def task_fingerprint(prompt, acceptance_criteria=None, verification_commands=None):
    """Step 5: "sha256:" + hex(SHA-256(canonical bytes))."""
    return PREFIX + hashlib.sha256(
        canonical_bytes(prompt, acceptance_criteria, verification_commands)).hexdigest()
