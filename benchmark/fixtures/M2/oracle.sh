#!/bin/sh
# M2: every call site moved to parse_iso_date, the legacy helper deleted, parse_iso_date untouched.
. "$FIXTURE_DIR/../../tools/oracle_lib.sh"
unchanged app/dates.py
python_oracle
