#!/bin/sh
# scripts/verify.sh: static repository checks (tasks.md T006; extended by T061, used by `dca verify`).
#
# POSIX sh. Runs against the repository that contains this script. Prints every failing
# check with the offending path, then exits 1; exits 0 when all checks pass. It reads no
# environment secrets and prints no file contents.
#
# Checks:
#   (a) construction-time Spec Kit skills never enter the runtime or the kit: no `speckit-*`
#       name or reference and no `.claude/skills` path anywhere under runtime/ (which includes
#       runtime/sandbox/kit/);
#   (b) runtime/agents/*.yaml never references the construction bootstrap
#       docker-agent.bootstrap.yaml;
#   (c) runtime/versions.yaml exists.

set -u

ROOT=$(CDPATH='' cd -- "$(dirname -- "$0")/.." && pwd) || exit 1
cd "$ROOT" || exit 1

failed=0

fail() {
    printf 'verify: FAIL %s\n' "$1" >&2
    failed=1
}

# (a) Spec Kit construction skills stay out of runtime/ and the kit.
if [ -d runtime ]; then
    paths=$(find runtime \( -name 'speckit-*' -o -path '*/.claude/skills' -o -path '*/.claude/skills/*' \) -print)
    if [ -n "$paths" ]; then
        set -f
        for p in $paths; do
            fail "(a) construction skill path under runtime/: $p"
        done
        set +f
    fi
    refs=$(grep -rIl -- 'speckit-' runtime 2>/dev/null || true)
    if [ -n "$refs" ]; then
        set -f
        for p in $refs; do
            fail "(a) speckit reference in runtime file: $p"
        done
        set +f
    fi
fi

# (b) Runtime agent configs never reference the construction bootstrap.
for f in runtime/agents/*.yaml; do
    [ -f "$f" ] || continue
    if grep -q -- 'docker-agent\.bootstrap\.yaml' "$f"; then
        fail "(b) runtime agent config references docker-agent.bootstrap.yaml: $f"
    fi
done

# (c) Exact version pins exist.
if [ ! -f runtime/versions.yaml ]; then
    fail "(c) missing runtime/versions.yaml"
fi

if [ "$failed" -ne 0 ]; then
    printf 'verify: FAILED\n' >&2
    exit 1
fi
printf 'verify: OK (a) no construction skills in runtime, (b) no bootstrap reference, (c) versions.yaml present\n'
exit 0
