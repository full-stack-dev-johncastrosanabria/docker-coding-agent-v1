#!/bin/sh
# K3: exactly the requested defaults changed, in defaults.ini; the loader and its checks untouched.
. "$FIXTURE_DIR/../../tools/oracle_lib.sh"
unchanged app/config.py
python_oracle
