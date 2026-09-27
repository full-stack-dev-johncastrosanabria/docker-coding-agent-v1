#!/bin/sh
# M4 (FR-001b): the call sites are not named in the task and sit in three unrelated packages, reached
# by three different import styles, so locating them needs repository-wide exploration - which the
# run must justify. Removing the helper is also a contract change, so the run is planned.
. "$FIXTURE_DIR/../../tools/oracle_lib.sh"
unchanged core/clock.py
python_oracle
if [ -e "$WORK/legacy" ]; then
    fail "the legacy package still exists"
fi
# Judged on the parsed modules, never on their text: a local name like `timestamp`, or a docstring
# that mentions the migration, must not decide the verdict. Tests are excluded, because the hidden
# tests deliberately assert that importing the retired package now fails.
python3 - "$WORK" <<'PY' || fail "a module still reaches the retired helper"
import ast
import os
import sys

root = sys.argv[1]
for current, directories, files in os.walk(root):
    directories[:] = [d for d in directories if d not in {"tests", ".git", "__pycache__"}]
    for name in sorted(files):
        if not name.endswith(".py") or name.startswith("test_"):
            continue
        path = os.path.join(current, name)
        relative = os.path.relpath(path, root)
        try:
            tree = ast.parse(open(path, encoding="utf-8").read())
        except (OSError, SyntaxError) as exc:
            sys.exit(f"{relative} cannot be parsed: {exc}")
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and (node.module or "").split(".")[0] == "legacy":
                sys.exit(f"{relative} still imports from the legacy package")
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name.split(".")[0] == "legacy":
                        sys.exit(f"{relative} still imports the legacy package")
                    if alias.asname == "stamp":
                        sys.exit(f"{relative} still calls the timestamp helper 'stamp'")
            if isinstance(node, ast.ImportFrom):
                for alias in node.names:
                    if alias.asname == "stamp" or (alias.asname is None and alias.name == "stamp"):
                        sys.exit(f"{relative} still imports a helper named 'stamp'")
            if isinstance(node, ast.Name) and node.id == "stamp":
                sys.exit(f"{relative} still calls stamp()")
            if isinstance(node, ast.Attribute) and node.attr == "stamp":
                sys.exit(f"{relative} still calls .stamp()")
PY
if [ -n "${RUN_OUT:-}" ]; then
    python3 "$FIXTURE_DIR/../../tools/planned_report.py" "$RUN_OUT" --repo-wide-exploration \
        || fail "the planned-work record is incomplete or does not justify the repository-wide search"
fi
