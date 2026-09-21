#!/bin/sh
# scripts/verify.sh: repository verification (tasks.md T006, extended by T061; used by `dca verify`).
#
# POSIX sh. Runs against the repository that contains this script. Prints every failing check with
# the offending path, then exits 1; exits 0 when all checks pass. It reads no environment secrets
# and prints no file contents.
#
# TWO LAYERS, AND THE SPLIT IS DELIBERATE.
#
#   * The shell checks below are the ones that are natural in `sh`: path shapes under runtime/ and
#     the presence of the pins file. They need nothing but a filesystem.
#   * Everything structured - the policy files, gate evidence, version pins, the effective Codex
#     tool lists, the trusted skill source - is in scripts/verify_checks.py, because expressing
#     those in `sh` would mean parsing JSON with `sed`, and a check nobody can read is a check
#     nobody maintains.
#
# `--static` restricts the run to the credential-free, daemon-free checks, which is what CI can
# run. Without it, the live checks run too: version pins against the installed tools, the live
# global network-policy fingerprint against the one the gate evidence recorded, backend sign-in
# read from safe structural fields only, and - for each AVAILABLE backend - the pinned binary's
# own `debug config`, `debug toolsets --json` and `debug skills` against a freshly staged kit.
# Those last three load the Codex team using the developer's existing sign-in, never a provider API
# key, and no token value is printed or logged.
#
# `--allow-drift` records a version-pin mismatch as a note instead of a failure. Nothing else is
# ever relaxed by it.
#
# Shell checks:
#   (a) construction-time Spec Kit skills never enter the runtime or the kit: no `speckit-*` name
#       or reference and no `.claude/skills` path anywhere under runtime/ (which includes
#       runtime/sandbox/kit/);
#   (b) runtime/agents/*.yaml never references the construction bootstrap
#       docker-agent.bootstrap.yaml;
#   (c) runtime/versions.yaml exists.

set -u

ROOT=$(CDPATH='' cd -- "$(dirname -- "$0")/.." && pwd) || exit 1
cd "$ROOT" || exit 1

PYTHON_ARGS=""
for arg in "$@"; do
    case "$arg" in
        --static|--allow-drift) PYTHON_ARGS="$PYTHON_ARGS $arg" ;;
        *) printf 'verify: unknown option %s\n' "$arg" >&2; exit 2 ;;
    esac
done

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

# (d) Everything structured. It reads gates/ and runs the contract tests, so it applies only to a
# full repository checkout. A partial tree (the isolation fixture copies scripts/ and runtime/
# only) says so out loud rather than reporting a pass it never computed.
if [ -f gates/eligibility.json ] && [ -d tests ]; then
    # shellcheck disable=SC2086
    if ! python3 scripts/verify_checks.py $PYTHON_ARGS; then
        failed=1
    fi
else
    printf 'verify: NOTE structured checks skipped: this tree has no gates/eligibility.json or tests/\n'
fi

if [ "$failed" -ne 0 ]; then
    printf 'verify: FAILED\n' >&2
    exit 1
fi
printf 'verify: OK (a) no construction skills in runtime, (b) no bootstrap reference, (c) versions.yaml present\n'
exit 0
