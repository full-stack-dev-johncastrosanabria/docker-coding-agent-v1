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

import collections
import os
import re

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


# --- what a tool call does to the workspace (T081) ---------------------------------------------------
#
# Moved here from dca/events.py so the host's FR-001 detector and the in-VM policy gate judge a
# shell command with ONE implementation. The gate cannot import events.py (host-only, and the
# trusted root holds only the reviewed gate modules); this module is already in that root.

#: Where the agent writes run evidence (the Context Record, the Plan, the report). A write confined
#: to it is not a workspace change.
SCRATCH_DIR = "/run/dca/out"

#: Programs that only look. Deliberately short: anything absent is treated as a mutation, which is
#: the fail-closed direction for an ordering rule (an early first-mutation can only make the
#: Context Record look late, never make a late one look early). `echo`, `printf` and `cd` (T075)
#: print or change the shell's directory only; the 005-007 runs showed them as false early
#: mutations. Any file they write goes through a redirection, which is checked separately.
READ_ONLY_PROGRAMS = frozenset({
    "ls", "cat", "head", "tail", "grep", "egrep", "fgrep", "rg", "ag", "find", "wc", "file",
    "stat", "pwd", "basename", "dirname", "tree", "du", "df", "diff", "cmp", "which", "type",
    "date", "whoami", "readlink", "realpath", "sort", "uniq", "cut", "tr", "nl", "column",
    "md5sum", "sha256sum", "shasum", "true", "false", "test", "echo", "printf", "cd",
})

#: `git` subcommands that only read. `git branch` and `git checkout` are absent on purpose: both
#: change refs or the worktree depending on their arguments.
READ_ONLY_GIT = frozenset({
    "status", "log", "diff", "show", "ls-files", "ls-tree", "rev-parse", "cat-file", "blame",
    "describe", "shortlog", "grep", "rev-list", "for-each-ref", "show-ref",
})


def _command_text(arguments):
    if not arguments:
        return None
    for key in ("command", "cmd", "script", "shell_command", "commandLine"):
        value = arguments.get(key)
        if isinstance(value, str) and value.strip():
            return value
    return None


def _target_paths(arguments):
    if not arguments:
        return []
    paths = []
    for key in ("path", "file_path", "filePath", "file", "target", "destination", "dest", "source"):
        value = arguments.get(key)
        if isinstance(value, str) and value:
            paths.append(value)
    # Multi-target tools (Codex's create_directory) name every target in a `paths` list.
    listed = arguments.get("paths")
    if isinstance(listed, list):
        paths += [item for item in listed if isinstance(item, str) and item]
    return paths


def _under_scratch(path, scratch_dir):
    normalized = os.path.normpath(path)
    scratch = os.path.normpath(scratch_dir)
    return normalized == scratch or normalized.startswith(scratch + os.sep)


#: Redirections that write no file: to /dev/null, or duplicating or closing a descriptor.
_HARMLESS_REDIRECTION = re.compile(r"(?:\d*|&)>>?\s*/dev/null\b|\d*>&(?:\d+|-)")

#: Arguments with which an otherwise inspecting program writes, deletes or runs something.
#: Short options match inside a cluster (`sort -uo out`); long ones also match `--opt=value`.
_WRITING_ARGUMENTS = {
    "sort": ("-o", "--output"),
    "tree": ("-o",),
    "file": ("-C", "--compile"),
    "rg": ("--pre",),
    "find": ("-delete", "-exec", "-execdir", "-ok", "-okdir", "-fprint", "-fprint0",
             "-fprintf", "-fls"),
}


def _redirects_to_a_file(command):
    """True when `command` sends output anywhere but /dev/null.

    The text is checked as written, so a `>` inside quotes or a heredoc body also counts. That
    over-reports on purpose: an early first mutation can only make the Context Record look late,
    and an extra mutation can only make a retry count sooner - never hide a write.
    """
    return ">" in _HARMLESS_REDIRECTION.sub(" ", command)


def _writes_by_argument(program, words):
    for word in words:
        for option in _WRITING_ARGUMENTS.get(program, ()):
            if option.startswith("--"):
                if word == option or word.startswith(option + "="):
                    return True
            elif len(option) == 2:
                if word.startswith("-") and not word.startswith("--") and option[1] in word[1:]:
                    return True
            elif word == option:
                return True
    # `uniq INPUT OUTPUT` writes OUTPUT.
    return program == "uniq" and len([word for word in words if not word.startswith("-")]) > 1


def shell_is_read_only(command):
    """True only when EVERY segment of `command` is known inspection that writes no file.

    An unparseable command is not read-only. That mirrors the policy gate's class 28: a command the
    host cannot analyse is never given the benefit of the doubt. Neither is one that redirects
    output to a file or passes an inspecting program an argument that makes it write.
    """
    parsed = parse(command)
    if not parsed.ok or not parsed.segments or _redirects_to_a_file(command):
        return False
    return all(_inspects(segment) for segment in parsed.segments)


def _inspects(segment):
    """True when one parsed segment only looks (its redirections are judged by the caller)."""
    program = (segment.program or "").rsplit("/", 1)[-1]
    words = list(segment.argv[1:])
    if program == "xargs":
        return _xargs_inspects(words)
    if program == "git":
        argv = [word for word in words if not word.startswith("-")]
        return bool(argv) and argv[0] in READ_ONLY_GIT and not any(
            word == "--output" or word.startswith("--output=") for word in words)
    if words == ["--version"] and "/" not in (segment.program or ""):
        return True     # a program on PATH asked only for its version (T075)
    return program in READ_ONLY_PROGRAMS and not _writes_by_argument(program, words)


#: `xargs` options whose value is the next word (GNU and BSD). Any other option stands alone.
_XARGS_VALUED = frozenset({
    "-a", "-d", "-E", "-I", "-J", "-L", "-n", "-P", "-R", "-S", "-s",
    "--arg-file", "--delimiter", "--eof", "--max-lines", "--max-args", "--max-procs",
    "--max-chars", "--process-slot-var",
})
_Wrapped = collections.namedtuple("_Wrapped", "program argv")


def _xargs_inspects(words):
    """`xargs` only looks when the command it runs only looks (T081).

    `find src -name "*.py" | xargs grep -l name` is a search, and the 009 runs showed it as a false
    early mutation. The command after xargs's options is judged exactly like a segment of its own,
    so `xargs sh -c ...`, `xargs rm` or `xargs sort -o out` stay mutations. With no command left,
    xargs runs `echo`.
    """
    index = 0
    while index < len(words) and words[index].startswith("-") and words[index] != "--":
        index += 2 if words[index] in _XARGS_VALUED else 1
    if index > len(words):
        return False    # an option whose value is missing: xargs itself refuses it
    if index < len(words) and words[index] == "--":
        index += 1
    wrapped = words[index:]
    return not wrapped or _inspects(_Wrapped(wrapped[0], wrapped))


#: An output redirection and its target. `>&N` (duplicating a descriptor) has no target.
_OUTPUT_REDIRECTION = re.compile(
    r"(?:\d+|&)?>>?\|?[ \t]*(?![&>])('[^']*'|\"[^\"]*\"|[^\s;&|<>()]+)")
#: A heredoc operator and its delimiter. A quoted delimiter turns off expansion in the body.
_HEREDOC = re.compile(r"<<(-?)[ \t]*(['\"]?)([A-Za-z_][A-Za-z0-9_]*)\2")
#: Characters that let the shell decide the path at run time: never a provable scratch target.
_EXPANDING = re.compile(r"[$`*?~\[{]")


def _without_heredoc_bodies(command):
    """`command` minus its heredoc bodies, or None when a body can run a command.

    A body is data unless its delimiter is unquoted and it contains a command substitution, which
    the shell runs while expanding the document.
    """
    kept, pending = [], []
    for line in command.split("\n"):
        if pending:
            delimiter, strip_tabs, expands = pending[0]
            if (line.lstrip("\t") if strip_tabs else line) == delimiter:
                pending.pop(0)
            elif expands and ("$(" in line or "`" in line):
                return None
            continue
        kept.append(line)
        pending += [(delimiter, dash == "-", not quote)
                    for dash, quote, delimiter in _HEREDOC.findall(line)]
    return None if pending else "\n".join(kept)


def shell_scratch_writes(command, scratch_dir=SCRATCH_DIR):
    """The scratch-dir paths `command` writes, when it can write nothing else; None otherwise.

    data-model.md: writes confined to `/run/dca/out/` are not workspace mutations. A shell command
    qualifies only when every segment is inspection or a `mkdir` of scratch directories, and every
    output redirection targets `/dev/null` or a literal absolute path inside the scratch dir.
    Anything the host cannot prove - a relative or variable target, a substitution, a leftover `>`
    it could not attribute - is not scratch-only.
    """
    parsed = parse(command)
    body = _without_heredoc_bodies(command)
    if not parsed.ok or not parsed.segments or body is None:
        return None
    body = _HARMLESS_REDIRECTION.sub(" ", body)
    matches = list(_OUTPUT_REDIRECTION.finditer(body))
    if body.count(">") != sum(match.group(0).count(">") for match in matches):
        return None
    written = []
    for match in matches:
        target = match.group(1).strip("'\"")
        if _EXPANDING.search(target) or not os.path.isabs(target) \
                or not _under_scratch(target, scratch_dir):
            return None
        written.append(os.path.normpath(target))
    for segment in parsed.segments:
        if (segment.program or "").rsplit("/", 1)[-1] == "mkdir":
            directories = [word for word in segment.argv[1:] if not word.startswith("-")]
            if not directories or any(_EXPANDING.search(d) or not os.path.isabs(d)
                                      or not _under_scratch(d, scratch_dir)
                                      for d in directories):
                return None
        elif not _inspects(segment):
            return None
    return written


def command_effect(command, scratch_dir=SCRATCH_DIR):
    """What one shell command does to the workspace: ("inspect", ()), ("scratch", paths) or
    ("mutate", ()).

    "inspect" means every segment is known inspection that writes no file; "scratch" means it can
    write only literal paths inside `scratch_dir`; anything else, including a command that cannot
    be parsed, is "mutate" - a build or test command is one too (data-model.md, first workspace
    mutation), because it can rewrite the tree.
    """
    if shell_is_read_only(command):
        return "inspect", ()
    written = shell_scratch_writes(command, scratch_dir)
    if written is None:
        return "mutate", ()
    return "scratch", tuple(written)
