#!/bin/sh
# M6: a behavioural change to what an existing operation refuses, which is a contract change, so the
# run is planned. Its independent review is the point: the record must show the review ran and left
# the candidate byte-identical (FR-020, FR-022).
. "$FIXTURE_DIR/../../tools/oracle_lib.sh"
python_oracle
if [ -n "${RUN_OUT:-}" ]; then
    python3 "$FIXTURE_DIR/../../tools/planned_report.py" "$RUN_OUT" \
        || fail "the planned-work record is incomplete"
fi
