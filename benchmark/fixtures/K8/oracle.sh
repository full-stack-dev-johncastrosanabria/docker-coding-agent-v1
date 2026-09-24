#!/bin/sh
# K8: parse_bool accepts the new spellings and still rejects the rest. It is a small task, so in an
# acceptance run it must be completed without an explicit plan (SC-010): classified direct, with no
# plan_ref and no plan.md among the run's outputs.
. "$FIXTURE_DIR/../../tools/oracle_lib.sh"
python_oracle
if [ -n "${RUN_OUT:-}" ]; then
    python3 - "$RUN_OUT" >&2 <<'PY' || fail "the small task was not completed plan-free (SC-010)"
import json
import os
import sys

try:
    with open(os.path.join(sys.argv[1], "report.json"), encoding="utf-8") as handle:
        report = json.load(handle)
except (OSError, ValueError) as exc:
    sys.exit(f"no readable report.json: {exc}")
classification = (report.get("classification") or {}).get("value")
if classification != "direct":
    sys.exit(f"classified {classification!r}, not direct")
if report.get("plan_ref") or os.path.exists(os.path.join(sys.argv[1], "plan.md")):
    sys.exit("an explicit plan artifact was produced")
PY
fi
