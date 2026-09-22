#!/bin/sh
# K3: the new tests pass on the real implementation and catch every hidden mutant of it.
. "$FIXTURE_DIR/../../tools/oracle_lib.sh"
unchanged textstats/words.py
ls "$WORK"/tests/test_*.py >/dev/null 2>&1 || fail "no test module was added under tests/"
python_suite || fail "the new tests fail on the unmodified implementation"
for mutant in "$FIXTURE_DIR"/mutants/*.py; do
    cp "$mutant" "$WORK/textstats/words.py"
    if python_suite; then
        fail "the tests do not detect the mutant $(basename "$mutant")"
    fi
done
echo "oracle: every mutant was detected" >&2
