#!/bin/sh
# R6: the root cause is fixed in parse_amount, not worked around in the invoice or the test.
. "$FIXTURE_DIR/../../tools/oracle_lib.sh"
python_oracle
