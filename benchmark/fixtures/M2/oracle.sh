#!/bin/sh
# M2 (FR-009): a coordinated two-module change that reads as a one-function fix. billing/eur.py
# independently implements the same cents rule, and tests/test_books.py already passes because both
# locales share it, so padding only billing/usd.py breaks that pre-existing test.
#
# The run must end PLANNED. It is NOT required to have escalated: FR-009 is conditional ("when
# discovered work exceeds the original classification"), and the bounded direct map already covers
# "the tests that cover" the changed file (runtime/skills/repository-navigation), so an agent that
# reads them classifies planned correctly on the first pass and must then OMIT escalated_from
# (runtime/skills/change-receipt). Demanding the artifact would demand a fabrication. What the oracle
# does demand is that a recorded escalation be true and consistent across the artifacts.
. "$FIXTURE_DIR/../../tools/oracle_lib.sh"
python_oracle
if [ -n "${RUN_OUT:-}" ]; then
    python3 "$FIXTURE_DIR/../../tools/planned_report.py" "$RUN_OUT" --escalation-consistent \
        || fail "the planned-work record is incomplete or its escalation record is inconsistent"
fi
