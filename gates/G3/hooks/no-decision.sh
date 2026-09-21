#!/bin/sh
# CASE C: the hook runs and succeeds but expresses NO permission decision. Absence of a decision
# must never be read as allow: it falls through to user confirmation, and `run --exec` rejects
# every confirmation request, so the call must not execute.
cat >/dev/null
printf 'dca-g3: this hook expresses no decision\n'
exit 0
