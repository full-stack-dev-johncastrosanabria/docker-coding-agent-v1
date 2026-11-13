#!/bin/sh
# M3 (US5, FR-004): the symptom is a late fee; the cause is three calls away, in the one helper
# every due date in a 119-file repository comes from - core.timeutil.workdays.next_business_day,
# which skips a weekend OR one holiday, but not a holiday that runs into a weekend. Two other
# modules also talk about business days and are deliberately different (the carriers' pickup
# calendar, the reports' weekday counts); neither is the cause, and neither may change.
#
# Correctness is the hidden tests: the helper itself, the reported invoice, and a caller the task
# never mentions (reminders), so a patch on the symptom in billing/ does not pass. The research
# record is research_record.py: the findings on record in the Context Record, and the researcher,
# when used, bounded and read-only. Delegating is not required to pass; T093 measures its rate. The task is planned (unclear root cause), so the planned-work record
# applies as well.
. "$FIXTURE_DIR/../../tools/oracle_lib.sh"
check "shipping/calendar.py was changed: carriers keep their own calendar" is_unchanged shipping/calendar.py
check "reporting/business_days.py was changed: report counts are not due dates" \
    is_unchanged reporting/business_days.py
verdict
python_oracle
[ -n "${RUN_OUT:-}" ] || fail "no run outputs: M3 is judged on the research record too"
python3 "$FIXTURE_DIR/../../tools/planned_report.py" "$RUN_OUT" \
    || fail "the planned-work record is incomplete"
python3 "$FIXTURE_DIR/research_record.py" "$RUN_OUT" \
    || fail "the investigation was not bounded and recorded"
