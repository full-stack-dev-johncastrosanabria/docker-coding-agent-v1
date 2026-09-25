#!/bin/sh
# M2 (FR-009): the task reads as a one-line format fix. Reading the package shows billing.summary
# repeats the same cents arithmetic while billing/__init__.py states that money is formatted in
# exactly one place, so padding is only correct when both surfaces move together and the rule ends up
# defined once. An acceptance run must therefore classify it planned, having ESCALATED from direct.
. "$FIXTURE_DIR/../../tools/oracle_lib.sh"
python_oracle
# "Defined in exactly one place", judged on the parsed module rather than on its text: a comment or a
# differently named shared helper must not decide the verdict.
python3 - "$WORK/billing/summary.py" <<'PY' || fail "the padding rule is not defined in exactly one place"
import ast
import sys

source = open(sys.argv[1], encoding="utf-8").read()
tree = ast.parse(source)
for node in ast.walk(tree):
    if isinstance(node, ast.BinOp) and isinstance(node.op, (ast.Mod, ast.FloorDiv, ast.Div)):
        sys.exit("billing/summary.py still computes cents itself")
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "divmod":
        sys.exit("billing/summary.py still splits cents itself with divmod")
    if isinstance(node, ast.FormattedValue) and node.format_spec is not None:
        spec = "".join(part.value for part in node.format_spec.values
                       if isinstance(part, ast.Constant) and isinstance(part.value, str))
        if "." in spec and spec.rstrip("f%eg").endswith((".1", ".2", ".3")):
            sys.exit("billing/summary.py formats money itself with a fractional format spec")
imported = {module.split(".")[0]
            for node in ast.walk(tree) if isinstance(node, ast.ImportFrom) and node.module
            for module in [node.module]}
imported |= {alias.name.split(".")[0]
             for node in ast.walk(tree) if isinstance(node, ast.Import) for alias in node.names}
if "billing" not in imported:
    sys.exit("billing/summary.py imports nothing from the billing package, so it cannot be reusing "
             "the one formatter")
PY
if [ -n "${RUN_OUT:-}" ]; then
    python3 "$FIXTURE_DIR/../../tools/planned_report.py" "$RUN_OUT" --escalated-from-direct \
        || fail "the planned-work record is incomplete or records no escalation from direct"
fi
