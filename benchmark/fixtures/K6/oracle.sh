#!/bin/sh
# K6: render_table works, and the change adds no dependency (FR-012): the dependency manifests are
# unchanged and the package imports nothing outside the standard library - not even optionally.
. "$FIXTURE_DIR/../../tools/oracle_lib.sh"
unchanged requirements.txt
unchanged pyproject.toml
(cd "$WORK" && python3 - 2>&1 <<'PY'
import ast
import os
import sys
import sysconfig

LOCAL = {"report", "__future__"}


def is_stdlib(name):
    if name in getattr(sys, "stdlib_module_names", ()) or name in sys.builtin_module_names:
        return True
    if hasattr(sys, "stdlib_module_names"):
        return False
    import importlib.util          # Python < 3.10: where the module would be imported from
    spec = importlib.util.find_spec(name)
    origin = (spec and spec.origin) or ""
    return spec is not None and (origin in ("built-in", "frozen") or (
        origin.startswith(sysconfig.get_paths()["stdlib"]) and "site-packages" not in origin))


found = set()
for root, _, files in os.walk("report"):
    for name in files:
        if name.endswith(".py"):
            with open(os.path.join(root, name), encoding="utf-8") as handle:
                tree = ast.parse(handle.read())
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    found |= {alias.name.split(".")[0] for alias in node.names}
                elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                    found.add(node.module.split(".")[0])
outside = sorted(name for name in found - LOCAL if not is_stdlib(name))
if outside:
    sys.exit("imports outside the standard library: " + ", ".join(outside))
PY
) >&2 || fail "the change adds a dependency (FR-012)"
python_oracle
