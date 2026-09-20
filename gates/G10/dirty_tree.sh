#!/bin/sh
# Gate-local dirty-tree decision primitive (tasks.md T012, Phase A).
#
# The smallest thing that can make the decision the launcher will have to make, so G10 can
# exercise the invariant rather than merely observe it. It is NOT the launcher: T064 owns
# preconditions, exit codes, ref parsing and reporting. This script only decides, for one
# repository, whether a run would be refused with no override.
#
#   dirty tracked change OR non-ignored untracked file  ->  REFUSE, exit 3
#   ignored files only, or a clean tree                 ->  ALLOW,  exit 0
#   the observation itself failed                       ->  exit 2 (fail closed, never ALLOW)
#
# `git status --porcelain=v1` excludes ignored files unless asked, which is exactly the
# distinction required: an ignored .env must never refuse a run. Output is one key=value line;
# file names are never printed, so a canary value can't leak through it.
#
# Usage: gates/G10/dirty_tree.sh <repository>

set -u

REFUSE=3
OBSERVATION_FAILED=2

[ $# -eq 1 ] || { printf 'usage: dirty_tree.sh <repository>\n' >&2; exit "$OBSERVATION_FAILED"; }
repo=$1

status=$(git -C "$repo" status --porcelain=v1 2>/dev/null) || exit "$OBSERVATION_FAILED"

# Untracked entries start with "??"; every other entry is a change to a tracked path.
untracked=$(printf '%s\n' "$status" | awk '/^\?\?/ {c++} END {print c+0}')
tracked=$(printf '%s\n' "$status" | awk 'NF && !/^\?\?/ {c++} END {print c+0}')

if [ "$tracked" -gt 0 ] || [ "$untracked" -gt 0 ]; then
    decision=refuse
else
    decision=allow
fi
# Distinct keys on their own lines: "tracked=" is a suffix of "untracked=", so a single line
# invites a greedy parse that reads one value as the other.
printf 'decision=%s\ntracked_changes=%s\nuntracked_files=%s\n' "$decision" "$tracked" "$untracked"

[ "$decision" = allow ] || exit "$REFUSE"
exit 0
