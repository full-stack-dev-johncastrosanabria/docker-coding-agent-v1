"""Conservative shell parser for the policy gate (tasks.md T028, contracts/policy-gate.md).

Standard library only, and it must stay that way: the gate imports this under `python3 -I` with
sys.path restricted to its trusted root plus the stdlib, so anything else is unimportable there.

IT ANSWERS ONE QUESTION: what programs would this shell string actually run? The gate classifies
those programs, so a wrong answer here is a policy decision made on a misreading. When the string
is ambiguous the only correct answer is "I don't know" - contracts/policy-gate.md maps that to
class 28, ASK - and never a plausible-looking guess. Every refusal carries a reason.

WHAT IT REFUSES, AND WHY EACH ONE MATTERS. A dynamic program name (`$CMD`) or a dynamic `-c`
payload cannot be resolved without running the shell, so the set of programs is unknown. A shell
reading a here-doc executes an opaque body. An unbalanced quote or an unterminated substitution
means the string does not mean what it appears to. In each case a guess would let a real command
through unclassified.

WHAT IT DELIBERATELY DOES NOT DO. It is not a shell and does not evaluate anything: no expansion,
no globbing, no arithmetic. It extracts programs and their literal arguments, follows command
substitution and `-c` payloads recursively, and stops.
"""

# The class contracts/policy-gate.md assigns to anything this parser refuses.
UNPARSEABLE_CLASS = 28

# Shells whose `-c` argument is a script, and which therefore have to be followed recursively.
SHELLS = frozenset({"sh", "bash", "dash", "zsh", "ksh", "ash",
                    "/bin/sh", "/bin/bash", "/bin/dash", "/bin/zsh", "/usr/bin/sh",
                    "/usr/bin/bash", "/usr/bin/env sh", "/usr/bin/env bash"})

# Prefixes that run another command and are not themselves the interesting program.
TRANSPARENT = frozenset({"command", "exec", "nohup", "nice", "time", "builtin"})

# Guards. The parser runs inside the gate on attacker-influenced input, so it must terminate.
MAX_DEPTH = 24
MAX_INPUT = 200_000
MAX_SEGMENTS = 2_000

_SEPARATORS = (";", "&&", "||", "|", "&", "\n")
_METACHARS = frozenset({"<", ">"})


class Segment:
    """One executable segment: its argv, and the program that argv would run."""

    def __init__(self, argv, assignments=None):
        self.argv = list(argv)
        self.assignments = dict(assignments or {})

    @property
    def program(self):
        return self.argv[0] if self.argv else None

    def __repr__(self):
        return f"Segment({self.argv!r})"


class Parsed:
    """The result. `ok` false means the gate must treat the command as class 28."""

    def __init__(self, ok, segments=None, reason=None):
        self.ok = ok
        self.segments = list(segments or [])
        self.reason = reason

    def __repr__(self):
        return f"Parsed(ok={self.ok}, segments={self.segments!r}, reason={self.reason!r})"


class _Refuse(Exception):
    def __init__(self, reason):
        super().__init__(reason)
        self.reason = reason


def _strip_heredoc_bodies(text):
    """Remove here-doc bodies so they are never read as commands, and flag shells fed by one.

    A here-doc body is data on stdin, not script - except when the program reading it IS a shell,
    which executes it. That case is unknowable from the text and is refused.
    """
    lines = text.split("\n")
    out, index = [], 0
    while index < len(lines):
        line = lines[index]
        delimiter = _heredoc_delimiter(line)
        out.append(line)
        index += 1
        if delimiter is None:
            continue
        # skip the body up to the delimiter line
        while index < len(lines) and lines[index].strip() != delimiter:
            index += 1
        index += 1          # drop the delimiter line itself
    return "\n".join(out)


def _heredoc_delimiter(line):
    """The here-doc delimiter this line opens, or None. Quotes around it are stripped."""
    position = 0
    in_single = in_double = False
    while position < len(line):
        char = line[position]
        if char == "\\" and not in_single:
            position += 2
            continue
        if char == "'" and not in_double:
            in_single = not in_single
        elif char == '"' and not in_single:
            in_double = not in_double
        elif char == "<" and not in_single and not in_double and line[position:position + 2] == "<<":
            rest = line[position + 2:].lstrip("-").strip()
            if not rest:
                return None
            word = rest.split()[0]
            return word.strip("'\"")
        position += 1
    return None


def _split_top_level(text):
    """Split into segment strings at unquoted separators, collecting substitutions separately.

    Returns (segment strings, substitution payloads). A substitution's payload is returned rather
    than inlined so the caller can parse it recursively: it is a command in its own right.
    """
    segments, subs = [], []
    current = []
    position = 0
    in_single = in_double = False
    length = len(text)

    while position < length:
        char = text[position]

        if in_single:
            if char == "'":
                in_single = False
            current.append(char)
            position += 1
            continue

        if char == "\\":
            current.append(char)
            if position + 1 < length:
                current.append(text[position + 1])
            position += 2
            continue

        if char == "'" and not in_double:
            in_single = True
            current.append(char)
            position += 1
            continue

        if char == '"':
            in_double = not in_double
            current.append(char)
            position += 1
            continue

        # `$(` and backticks run a command even inside double quotes.
        if char == "$" and text[position:position + 2] == "$(":
            payload, position = _read_balanced(text, position + 2, "(", ")")
            subs.append(payload)
            current.append("\x00SUB\x00")
            continue
        if char == "`":
            payload, position = _read_backtick(text, position + 1)
            subs.append(payload)
            current.append("\x00SUB\x00")
            continue

        if in_double:
            current.append(char)
            position += 1
            continue

        if char == "#" and (not current or current[-1].isspace()):
            while position < length and text[position] != "\n":
                position += 1
            continue

        matched = None
        for separator in _SEPARATORS:
            if text.startswith(separator, position):
                matched = separator
                break
        # `2>&1` is one redirection, not a background separator followed by a command.
        if matched == "&" and current and current[-1] in "<>":
            matched = None
        if matched:
            segments.append("".join(current))
            current = []
            position += len(matched)
            continue

        current.append(char)
        position += 1

    if in_single or in_double:
        raise _Refuse("unbalanced quote")
    segments.append("".join(current))
    return [s for s in segments if s.strip()], subs


def _read_balanced(text, start, opener, closer):
    """The text inside a balanced $( … ), and the index just past the closer."""
    depth = 1
    position = start
    in_single = in_double = False
    while position < len(text):
        char = text[position]
        if char == "\\" and not in_single:
            position += 2
            continue
        if in_single:
            if char == "'":
                in_single = False
            position += 1
            continue
        if char == "'" and not in_double:
            in_single = True
        elif char == '"':
            in_double = not in_double
        elif not in_double:
            if char == opener:
                depth += 1
            elif char == closer:
                depth -= 1
                if depth == 0:
                    return text[start:position], position + 1
        position += 1
    raise _Refuse("unterminated command substitution")


def _read_backtick(text, start):
    position = start
    while position < len(text):
        if text[position] == "\\":
            position += 2
            continue
        if text[position] == "`":
            return text[start:position], position + 1
        position += 1
    raise _Refuse("unterminated backtick substitution")


def _tokenize(segment):
    """Literal words of one segment. Substitution placeholders and redirections are dropped."""
    words, current, started = [], [], False
    position = 0
    in_single = in_double = False
    length = len(segment)

    while position < length:
        char = segment[position]

        if in_single:
            if char == "'":
                in_single = False
            else:
                current.append(char)
            position += 1
            continue

        if char == "\\":
            if position + 1 < length:
                current.append(segment[position + 1])
                started = True
            position += 2
            continue

        if char == "'" and not in_double:
            in_single = True
            started = True
            position += 1
            continue

        if char == '"':
            in_double = not in_double
            started = True
            position += 1
            continue

        if not in_double and char.isspace():
            if started:
                words.append("".join(current))
                current, started = [], False
            position += 1
            continue

        if not in_double and char in _METACHARS:
            # a redirection: drop the operator and its target word
            if started:
                words.append("".join(current))
                current, started = [], False
            position += 1
            while position < length and segment[position] in "<>&":
                position += 1
            while position < length and segment[position].isspace():
                position += 1
            while position < length and not segment[position].isspace():
                position += 1
            continue

        current.append(char)
        started = True
        position += 1

    if started:
        words.append("".join(current))
    return words


def _is_dynamic(word):
    """Does this word's value depend on expansion we cannot perform?"""
    return "$" in word or "\x00SUB\x00" in word


def _strip_prefixes(words):
    """Drop leading assignments and transparent prefixes; return (assignments, remaining words)."""
    assignments = {}
    index = 0
    while index < len(words):
        word = words[index]
        if word == "env":
            index += 1
            while index < len(words) and (words[index].startswith("-")
                                          or ("=" in words[index]
                                              and not words[index].startswith("="))):
                if "=" in words[index] and not words[index].startswith("-"):
                    name, _, value = words[index].partition("=")
                    assignments[name] = value
                index += 1
            continue
        if "=" in word and not word.startswith("=") and not word.startswith("-") \
                and word.split("=")[0].replace("_", "a").isalnum():
            name, _, value = word.partition("=")
            assignments[name] = value
            index += 1
            continue
        if word in TRANSPARENT:
            index += 1
            continue
        break
    return assignments, words[index:]


def _parse_words(words, depth, out):
    """Turn one segment's words into a Segment, following `-c` payloads recursively."""
    assignments, words = _strip_prefixes(words)
    if not words:
        # `FOO=1` alone runs nothing, so it contributes no segment to classify.
        return

    program = words[0]
    if _is_dynamic(program):
        raise _Refuse("the program name depends on expansion")

    out.append(Segment(words, assignments))

    if program in SHELLS or program.rsplit("/", 1)[-1] in SHELLS:
        _follow_dash_c(words, depth, out)
    elif program == "eval":
        payload = words[1:]
        if not payload:
            return
        if any(_is_dynamic(word) for word in payload):
            raise _Refuse("eval payload depends on expansion")
        _parse_into(" ".join(payload), depth + 1, out)


def _follow_dash_c(words, depth, out):
    for index, word in enumerate(words[1:], start=1):
        if word == "-c":
            if index + 1 >= len(words):
                raise _Refuse("-c with no payload")
            payload = words[index + 1]
            if _is_dynamic(payload):
                raise _Refuse("-c payload depends on expansion")
            _parse_into(payload, depth + 1, out)
            return
        if word.startswith("-"):
            continue
        return      # first non-flag operand is a script path, not an inline payload


def _parse_into(text, depth, out):
    if depth > MAX_DEPTH:
        raise _Refuse("nesting too deep")
    if len(out) > MAX_SEGMENTS:
        raise _Refuse("too many segments")

    text = _strip_heredoc_bodies(text)
    segment_strings, subs = _split_top_level(text)

    for segment in segment_strings:
        words = _tokenize(segment)
        if not words:
            continue
        # A shell fed by a here-doc executes an opaque body.
        if (words[0] in SHELLS or words[0].rsplit("/", 1)[-1] in SHELLS) \
                and _heredoc_delimiter(segment) is not None:
            raise _Refuse("a shell reading a here-doc executes an opaque body")
        _parse_words(words, depth, out)

    for payload in subs:
        _parse_into(payload, depth + 1, out)


def parse(command):
    """(Parsed) every program `command` would run, or a refusal with a reason."""
    if not isinstance(command, str):
        return Parsed(False, reason="command is not a string")
    if len(command) > MAX_INPUT:
        return Parsed(False, reason="command is too long to analyse")
    if not command.strip():
        return Parsed(False, reason="empty command")

    out = []
    try:
        _parse_into(command, 0, out)
    except _Refuse as refusal:
        return Parsed(False, reason=refusal.reason)
    except RecursionError:
        return Parsed(False, reason="nesting too deep")
    return Parsed(True, segments=out)


def programs(command):
    """Convenience: the program names, or None if the command is unparseable."""
    parsed = parse(command)
    if not parsed.ok:
        return None
    return [segment.program for segment in parsed.segments if segment.program]
