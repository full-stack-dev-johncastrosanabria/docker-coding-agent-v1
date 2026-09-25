#!/bin/sh
# M1: the per-line discount is ONE rule, threaded through lines, invoice and render, so no component
# can disagree. It is a multi-component change, so an acceptance run must plan it before the first
# workspace mutation and have it independently reviewed (FR-008, FR-020, FR-022).
. "$FIXTURE_DIR/../../tools/oracle_lib.sh"
python_oracle
if [ -n "${RUN_OUT:-}" ]; then
    python3 "$FIXTURE_DIR/../../tools/planned_report.py" "$RUN_OUT" \
        || fail "the planned-work record is incomplete"
fi
