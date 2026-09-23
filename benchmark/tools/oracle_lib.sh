# Shared oracle helpers, sourced by benchmark/fixtures/<id>/oracle.sh (benchmark/FORMAT.md).
#
# An oracle never trusts the candidate's tests alone. It copies the candidate into a scratch
# directory (the candidate itself is read-only), then:
#   * runs the candidate's own suite as delivered;
#   * restores the fixture's ORIGINAL tests over the candidate's copies, so a weakened or deleted
#     test cannot hide a bug, and adds the fixture's hidden tests, which the agent never saw.
# POSIX sh, python3 and node only: the same script runs on the host for golden patches and in the
# pinned sandbox base image, offline, for agent-produced candidates.
set -eu
: "${CANDIDATE_DIR:?CANDIDATE_DIR is required}" "${FIXTURE_DIR:?FIXTURE_DIR is required}"
export PYTHONDONTWRITEBYTECODE=1
WORK=$(mktemp -d)
trap 'rm -rf "$WORK"' EXIT
cp -R "$CANDIDATE_DIR"/. "$WORK"/
rm -rf "$WORK/.git"

fail() {
    echo "oracle: $*" >&2
    exit 1
}

# unchanged <path>: the candidate did not modify a file the task says to leave alone.
unchanged() {
    cmp -s "$FIXTURE_DIR/seed/$1" "$WORK/$1" || fail "$1 was modified"
}

python_suite() {
    (cd "$WORK" && python3 -m unittest discover -s tests -t . 2>&1) >&2
}

# python_oracle: own tests, then the original and hidden tests, all under unittest.
python_oracle() {
    python_suite || fail "the candidate's own tests fail"
    if [ -d "$FIXTURE_DIR/seed/tests" ]; then
        cp "$FIXTURE_DIR"/seed/tests/*.py "$WORK/tests/"
    fi
    cp "$FIXTURE_DIR"/hidden/test_*.py "$WORK/tests/"
    python_suite || fail "the original or hidden tests fail"
}

# node_oracle: the same, with the built-in node test runner.
node_oracle() {
    (cd "$WORK" && node --test 2>&1) >&2 || fail "the candidate's own tests fail"
    cp "$FIXTURE_DIR"/seed/test/*.js "$WORK/test/"
    cp "$FIXTURE_DIR"/hidden/*.test.js "$WORK/test/"
    (cd "$WORK" && node --test 2>&1) >&2 || fail "the original or hidden tests fail"
}

# only_failing <test-id>...: the whole unittest suite runs in the scratch copy, and exactly these
# tests fail - a failure that already existed before the change stays what it was, and nothing new
# fails or errors (FR-019). Ids are unittest ids: tests.test_module.Class.test_name.
only_failing() {
    (cd "$WORK" && python3 - "$@" 2>&1 <<'PY'
import sys
import unittest

expected = set(sys.argv[1:])
suite = unittest.defaultTestLoader.discover("tests", top_level_dir=".")
result = unittest.TextTestRunner(stream=sys.stderr, verbosity=1).run(suite)
failing = {test.id() for test, _ in result.failures + result.errors}
new, fixed = sorted(failing - expected), sorted(expected - failing)
if new:
    print("new failures: " + ", ".join(new))
if fixed:
    print("the pre-existing failure no longer fails: " + ", ".join(fixed))
sys.exit(1 if new or fixed or not result.testsRun else 0)
PY
    ) >&2 || fail "the suite does not fail exactly where it failed before the change"
}
