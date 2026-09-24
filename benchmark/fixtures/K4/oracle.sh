#!/bin/sh
# K4: the README's "Splitting amounts" section documents the leftover-cent rule, the candidate's
# own tests pin it (they reject a mutant that gives the leftover to the last parts), every original
# test is still there, and neither the code nor the rest of the README changed.
. "$FIXTURE_DIR/../../tools/oracle_lib.sh"
unchanged splitter/amounts.py
python3 - "$FIXTURE_DIR/seed" "$WORK" >&2 <<'PY' || fail "the README or the original tests are not as required"
import os
import re
import sys

seed, work = sys.argv[1], sys.argv[2]
SECTION = re.compile(r"(^## Splitting amounts\n.*?)(?=^## |\Z)", re.M | re.S)


def read(root, path):
    with open(f"{root}/{path}", encoding="utf-8") as handle:
        return handle.read()


before, after = read(seed, "README.md"), read(work, "README.md")
section = SECTION.search(after)
if not section:
    sys.exit("README.md has no 'Splitting amounts' section")
if SECTION.sub("", before) != SECTION.sub("", after):
    sys.exit("README.md changed outside the 'Splitting amounts' section")
text = section.group(1).lower()
if "first" not in text or not re.search(r"left ?over|remainder|remaining|extra cent", text):
    sys.exit("the 'Splitting amounts' section does not say the leftover cents go to the first parts")
original = set(re.findall(r"def (test_\w+)", read(seed, "tests/test_amounts.py")))
kept = set(re.findall(r"def (test_\w+)", "".join(
    read(work, f"tests/{name}") for name in os.listdir(f"{work}/tests") if name.endswith(".py"))))
missing = sorted(original - kept)
if missing:
    sys.exit("original tests were removed: " + ", ".join(missing))
PY
python_suite || fail "the candidate's own tests fail"
cp "$FIXTURE_DIR/mutants/leftover_to_last.py" "$WORK/splitter/amounts.py"
if python_suite; then
    fail "the candidate's tests do not pin who receives the leftover cents (the mutant passes)"
fi
