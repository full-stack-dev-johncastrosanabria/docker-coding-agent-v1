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

# is_unchanged <path>: the same test as a predicate, for `check` (below).
is_unchanged() {
    cmp -s "$FIXTURE_DIR/seed/$1" "$WORK/$1"
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

# same_as_seed: the candidate IS the seed - no file added, removed or changed. For a fixture whose
# correct outcome leaves the repository untouched (a blocked or failed run with an empty change set),
# this is the repository-side half of "empty change set"; the report's change_set is the other half.
# Bytecode and Finder litter are not repository content and are ignored on both sides. It RETURNS
# its verdict rather than exiting, so an oracle can report every broken expectation, not only the
# first (see `check` below).
same_as_seed() {
    python3 - "$FIXTURE_DIR/seed" "$WORK" >&2 <<'PY' || { echo "oracle: the candidate is not the unchanged seed" >&2; return 1; }
import os
import sys


def files(root):
    found = {}
    for current, directories, names in os.walk(root):
        directories[:] = [d for d in directories if d not in ("__pycache__", ".git")]
        for name in names:
            if name == ".DS_Store" or name.endswith(".pyc"):
                continue
            path = os.path.join(current, name)
            with open(path, "rb") as handle:
                found[os.path.relpath(path, root)] = handle.read()
    return found


seed, candidate = files(sys.argv[1]), files(sys.argv[2])
added = sorted(set(candidate) - set(seed))
removed = sorted(set(seed) - set(candidate))
changed = sorted(p for p in set(seed) & set(candidate) if seed[p] != candidate[p])
for label, paths in (("added", added), ("removed", removed), ("changed", changed)):
    if paths:
        print(f"{label}: {', '.join(paths)}")
sys.exit(1 if added or removed or changed else 0)
PY
}

# check <description> <command...>: run one expectation and REMEMBER a failure instead of exiting, so
# a candidate that is wrong in several ways shows every way it is wrong. `verdict` then fails the
# oracle if any check did. The US3 fixtures use it: their bad cases are built to fail for one
# specific reason, and the oracle's output has to show that reason.
FAILED_CHECKS=""
check() {
    description=$1
    shift
    if ! "$@"; then
        echo "oracle: $description" >&2
        FAILED_CHECKS="${FAILED_CHECKS}${FAILED_CHECKS:+; }$description"
    fi
}

verdict() {
    [ -z "$FAILED_CHECKS" ] || fail "$FAILED_CHECKS"
}
