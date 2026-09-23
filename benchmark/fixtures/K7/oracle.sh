#!/bin/sh
# K7: the new rule follows the repository's rule convention (FR-013): a function in
# checks/rules.py, registered with @rule under the next code, returning Finding values in the
# existing message style. The registry and the lint entry point are not special-cased.
. "$FIXTURE_DIR/../../tools/oracle_lib.sh"
unchanged checks/registry.py
unchanged checks/lint.py
python_oracle
