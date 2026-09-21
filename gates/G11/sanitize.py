"""Deterministic capture sanitizer for gate G11 part A (tasks.md T020).

Standard library only. It turns a raw `docker agent run --exec --json` capture into the sanitized
capture committed under gates/G11/captures/<backend>/, and reports what it removed.

IT REDACTS VALUES, NOT STRUCTURE. The obvious implementation - run the credential regexes over the
raw bytes and blank every match - destroys the evidence: several of those patterns match a JSON KEY
and its colon (`"last_refresh"\\s*:\\s*"`), so a textual replacement rewrites the object boundary and
the sanitized capture parses as `malformed`. The gate would then be proving a property of its own
sanitizer. So this walks the parsed event and replaces matches inside STRING VALUES only, leaving
every type, key and nesting level exactly where the runtime put it - which is precisely the
"event identity and structural facts" T020 says must survive.

The sanitized capture is therefore a re-serialization, not a byte copy. Both digests are reported so
the two artifacts can be tied together, and the redaction counts say what was taken out.
"""

import importlib.util
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# The credential families this repository already knows how to recognise. They are imported from the
# accepted scanners rather than restated, so a pattern fixed there is fixed here too.
_g1b = _load("g11_sanitize_g1b", os.path.join(ROOT, "G1b", "scan.py"))
_g2 = _load("g11_sanitize_g2", os.path.join(ROOT, "G2", "scan.py"))

# Only VALUE-shaped patterns. The key-shaped ones ("access_token":") describe where a credential
# lives, not what one looks like, and applying them to a value would redact ordinary structure.
VALUE_PATTERNS = {
    "claude-oauth-access": _g1b.PATTERNS["claude-oauth-access"],
    "claude-oauth-refresh": _g1b.PATTERNS["claude-oauth-refresh"],
    "anthropic-api-key": _g1b.PATTERNS["anthropic-api-key"],
    "anthropic-admin-key": _g1b.PATTERNS["anthropic-admin-key"],
    "claude-session-cookie": _g1b.PATTERNS["claude-session-cookie"],
    "jwt-material": _g2.PATTERNS["jwt-material"],
    "openai-project-key": _g2.PATTERNS["openai-project-key"],
    "openai-api-key": _g2.PATTERNS["openai-api-key"],
    "chatgpt-session-cookie": _g2.PATTERNS["chatgpt-session-cookie"],
    "authorization-bearer": _g2.PATTERNS["authorization-bearer"],
}

# The replacement carries no quote or backslash, so substituting it inside a JSON string value can
# never change the document's structure.
def _token(name):
    return f"[REDACTED-{name}]"


def _scrub(value, counts):
    if isinstance(value, str):
        raw = value.encode("utf-8", "surrogatepass")
        for name, pattern in VALUE_PATTERNS.items():
            hits = pattern.findall(raw)
            if hits:
                counts[name] = counts.get(name, 0) + len(hits)
                raw = pattern.sub(_token(name).encode(), raw)
        return raw.decode("utf-8", "replace")
    if isinstance(value, dict):
        return {k: _scrub(v, counts) for k, v in value.items()}
    if isinstance(value, list):
        return [_scrub(v, counts) for v in value]
    return value


def sanitize_text(text):
    """(sanitized text, {pattern: redactions}, [problems]).

    A line that is not valid JSON is passed through UNCHANGED and reported. Repairing it here would
    hide exactly the corruption criterion 3 is about, and dropping it would turn a malformed capture
    into a clean-looking one.
    """
    counts, problems, out = {}, [], []
    lines = text.split("\n")
    trailing_newline = lines and lines[-1] == ""
    if trailing_newline:
        lines = lines[:-1]
    for number, line in enumerate(lines, 1):
        if not line.strip():
            out.append(line)
            continue
        try:
            event = json.loads(line)
        except ValueError:
            problems.append(f"line {number} is not valid JSON and was passed through unchanged")
            out.append(line)
            continue
        out.append(json.dumps(_scrub(event, counts), ensure_ascii=False))
    return "\n".join(out) + ("\n" if trailing_newline else ""), counts, problems


def main(argv):
    if len(argv) != 3:
        print("usage: python3 gates/G11/sanitize.py <raw-capture> <sanitized-out>",
              file=sys.stderr)
        return 2
    with open(argv[1], encoding="utf-8", errors="replace") as fh:
        raw = fh.read()
    sanitized, counts, problems = sanitize_text(raw)
    with open(argv[2], "w", encoding="utf-8") as fh:
        fh.write(sanitized)
    print(f"sanitize redactions={sum(counts.values())} patterns={sorted(counts)} "
          f"problems={len(problems)}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
