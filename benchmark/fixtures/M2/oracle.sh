#!/bin/sh
# M2 (FR-009): the task names one function in one module, and a bounded task-scoped read of
# billing/usd.py and its test genuinely supports a DIRECT classification. The coupling is not hidden
# and it is not in the prompt - it is a deterministic repository fact the agent meets during normal
# verification: tests/test_cents_parity.py already passes because BOTH locales share the unpadded
# rule, so padding only billing/usd.py makes that pre-existing test fail and reveals that
# billing/eur.py must change with it. That is work discovered after classification, which exceeds
# direct bounds, so the run must escalate direct -> planned exactly once and record it.
. "$FIXTURE_DIR/../../tools/oracle_lib.sh"
python_oracle
if [ -n "${RUN_OUT:-}" ]; then
    python3 "$FIXTURE_DIR/../../tools/planned_report.py" "$RUN_OUT" --escalated-from-direct \
        || fail "the planned-work record is incomplete or records no real escalation from direct"
fi
