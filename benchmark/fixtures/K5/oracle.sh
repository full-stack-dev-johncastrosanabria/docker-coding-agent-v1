#!/bin/sh
# K5: blank and whitespace-only lines are skipped and the rest numbered from 1, text unchanged.
# The repository has no deterministic check for this behavior, so in an acceptance run the report
# must use FR-014a alternative verification: type alternative, with its definition and the stated
# limitation in both the completion report and the Context Record written before the first change.
. "$FIXTURE_DIR/../../tools/oracle_lib.sh"
unchanged notes.txt
[ ! -e "$WORK/tests" ] || fail "a test suite was added"
(cd "$WORK" && python3 - 2>&1 <<'PY'
import subprocess
import sys

CASES = [
    ("first\n\n  \t\nsecond\n\n\n    indented note\nlast  with  spaces\n",
     "1. first\n2. second\n3.     indented note\n4. last  with  spaces\n"),
    ("only\n", "1. only\n"),
    ("\n \n\t\n", ""),
    ("no final newline", "1. no final newline\n"),
]
for index, (notes, expected) in enumerate(CASES):
    path = f"hidden-notes-{index}.txt"
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(notes)
    run = subprocess.run([sys.executable, "notes.py", path], capture_output=True, text=True)
    if run.returncode != 0 or run.stdout != expected:
        sys.exit(f"notes.py {notes!r}: exit {run.returncode}, printed {run.stdout!r}, "
                 f"expected {expected!r} {run.stderr[-300:]}")
default = subprocess.run([sys.executable, "notes.py"], capture_output=True, text=True)
if default.stdout != "1. buy coffee\n2. call the plumber\n3. renew the passport\n":
    sys.exit(f"notes.py with the default file printed {default.stdout!r}")
PY
) >&2 || fail "notes.py does not print the notes as required"
if [ -n "${RUN_OUT:-}" ]; then
    python3 - "$RUN_OUT" >&2 <<'PY' || fail "the run does not record FR-014a alternative verification"
import json
import sys

PLACEHOLDER = "not recorded by the agent"   # what the host fills in when the agent said nothing


def load(name):
    try:
        with open(f"{sys.argv[1]}/{name}", encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, ValueError) as exc:
        sys.exit(f"no readable {name}: {exc}")


def stated(value):
    return isinstance(value, str) and value.strip() not in ("", PLACEHOLDER)


verification = load("report.json").get("verification") or {}
if verification.get("type") != "alternative":
    sys.exit(f"verification.type is {verification.get('type')!r}: no repository-established "
             "deterministic check exists, so it must be alternative (FR-014a)")
for key in ("alternative_definition", "limitation"):
    if not stated(verification.get(key)):
        sys.exit(f"the completion report does not state verification.{key}")
approach = load("context.json").get("verification_approach") or {}
if approach.get("type") != "alternative" or not (
        stated(approach.get("definition")) and stated(approach.get("limitation"))):
    sys.exit("the Context Record does not define the alternative approach and its limitation "
             "before the first change: " + json.dumps(approach)[:300])
PY
fi
