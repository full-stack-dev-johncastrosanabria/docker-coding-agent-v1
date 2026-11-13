"""S5b's control-plane verdict: Gate G9's own oracle, applied to this run's observations.

Nothing is classified here. `tools/g9/oracle.py` in the seed is a byte copy of gates/G9/oracle.py
(tests/unit/test_safety_fixtures.py holds them equal), and this module only gathers what it needs:

  * the workload variants - the JSON lines the LAUNCHER's run of tools/control_plane_probe.py
    printed inside the sandbox (report.verification.checks[].output_ref);
  * the positive control - the host's record that the agent path produced the mediation signal on
    the same endpoint in the same sandbox, `control-plane-positive.json` in the run outputs.

Fail-closed like G9: no positive control, an ambiguous variant or a missing privilege level fails.
"""

import importlib.util
import json
import os
import sys

COMMAND = "python3 tools/control_plane_probe.py"


def load_oracle(fixture_dir):
    spec = importlib.util.spec_from_file_location(
        "g9_oracle", os.path.join(fixture_dir, "seed", "tools", "g9", "oracle.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main(fixture_dir, run_out):
    oracle = load_oracle(fixture_dir)
    with open(os.path.join(run_out, "report.json"), encoding="utf-8") as handle:
        report = json.load(handle)
    probes = oracle.probes_for(oracle.load_probes(
        os.path.join(fixture_dir, "seed", "tools", "g9", "probes.json")), report.get("backend"))
    check = next((c for c in (report.get("verification") or {}).get("checks") or []
                  if c.get("command_or_method") == COMMAND and c.get("executed_by") == "launcher"),
                 None)
    if check is None or check.get("result") != "pass" or not check.get("output_ref"):
        print(f"control plane: the launcher did not run and pass {COMMAND!r}")
        return 1
    by_probe = {}
    with open(os.path.join(run_out, check["output_ref"]), encoding="utf-8") as handle:
        for line in handle:
            try:
                record = json.loads(line)
            except ValueError:
                continue
            if isinstance(record, dict) and record.get("probe"):
                by_probe.setdefault(record["probe"], []).append(record)
    try:
        with open(os.path.join(run_out, "control-plane-positive.json"), encoding="utf-8") as handle:
            positive = json.load(handle)
    except (OSError, ValueError):
        positive = {}
    status, results = oracle.evaluate_backend(probes, positive, by_probe)
    for probe_id, (holds, problems, _) in results.items():
        for problem in problems:
            print(f"VIOLATION G9: {probe_id}: {problem}")
    return 0 if status == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1], sys.argv[2]))
