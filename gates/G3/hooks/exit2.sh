#!/bin/sh
# CASE B: exit 2 blocks the tool call (E18). No decision is printed; the exit code is the signal.
cat >/dev/null
printf 'dca-g3: blocking this tool call\n' >&2
exit 2
