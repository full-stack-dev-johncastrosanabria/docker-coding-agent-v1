#!/bin/sh
# R5: exactly the three requested settings changed, the loader still validates, code untouched.
. "$FIXTURE_DIR/../../tools/oracle_lib.sh"
unchanged service/settings.py
python_oracle
