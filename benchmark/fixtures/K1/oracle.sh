#!/bin/sh
# K1: the multi-entry fix holds for the original and hidden tests, and the suite fails exactly
# where it failed before the change: the unrelated week-label failure is left alone and nothing
# new fails. In an acceptance run the report's verification baseline must also record that failure
# as pre-existing (FR-019): a failing pre-change check that covers tests/test_week.py.
. "$FIXTURE_DIR/../../tools/oracle_lib.sh"
unchanged timesheet/week.py
unchanged tests/test_week.py
PRE_EXISTING=tests.test_week.TestWeekLabel.test_year_boundary_uses_iso_week_year
only_failing "$PRE_EXISTING"    # the suite as delivered
cp "$FIXTURE_DIR/seed/tests/test_hours.py" "$FIXTURE_DIR/hidden/test_hidden_hours.py" "$WORK/tests/"
only_failing "$PRE_EXISTING"    # with the original and hidden hours tests
if [ -n "${RUN_OUT:-}" ]; then
    python3 - "$RUN_OUT/report.json" >&2 <<'PY' || fail "the verification baseline does not record the pre-existing failure (FR-019)"
import json
import re
import sys

try:
    with open(sys.argv[1], encoding="utf-8") as handle:
        report = json.load(handle)
except (OSError, ValueError) as exc:
    sys.exit(f"no readable report.json: {exc}")
baseline = (report.get("verification") or {}).get("baseline") or []


def covers_week_tests(command):
    # The week tests by name, the whole suite (discover), or unittest/pytest with no narrower target.
    if "test_week" in command or "discover" in command:
        return True
    runner = re.search(r"\b(unittest|pytest)\b([^|;&]*)", command)
    return bool(runner) and not re.search(r"\btests?[./]test_\w+", runner.group(2))


if not any(isinstance(check, dict) and check.get("result") in ("fail", "error", "partial")
           and covers_week_tests(str(check.get("command_or_method") or ""))
           for check in baseline):
    sys.exit("verification.baseline has no failing pre-change check covering tests/test_week.py: "
             + json.dumps(baseline)[:400])
PY
fi
