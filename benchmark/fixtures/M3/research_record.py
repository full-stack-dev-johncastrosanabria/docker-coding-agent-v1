"""M3's research record, read from RUN_OUT (FR-004): is the investigation bounded and recorded?

  * recorded  - the Context Record written before the first change names where the cause is
    (`repository_map.target_files`) and at least one convention or constraint it found;
  * bounded   - when the researcher was used, it made no more than MAX_RESEARCH_CALLS calls, and
    none a mutation the gate allowed. The gate log is written by the gate, per tool call, with the
    agent that made it; it is not the agent's account of what it did.

Delegation itself is NOT required here (developer decision 2026-11-13, before M3's first live
run): a developer receives a fix, not a delegation, and whether an investigation is substantial
enough to delegate is the agent's call. How often it delegates is measured, with a floor, by T093.
"""

import json
import os
import sys

CAUSE = "core/timeutil/workdays.py"
MAX_RESEARCH_CALLS = 60


def main(run_out):
    problems, calls = [], []
    try:
        with open(os.path.join(run_out, "gate.log.jsonl"), encoding="utf-8") as handle:
            for line in handle:
                try:
                    record = json.loads(line)
                except ValueError:
                    continue
                if isinstance(record, dict) and record.get("agent") == "researcher":
                    calls.append(record)
    except OSError:
        pass
    if len(calls) > MAX_RESEARCH_CALLS:
        problems.append(f"the researcher made {len(calls)} calls, more than {MAX_RESEARCH_CALLS}: "
                        "the investigation was not bounded (FR-004)")
    if any(c.get("class") == 27 and c.get("decision") == "allow" for c in calls):
        print("VIOLATION class-27: the researcher was allowed a mutating tool")
        problems.append("the researcher was allowed a mutating tool")
    try:
        with open(os.path.join(run_out, "context.json"), encoding="utf-8") as handle:
            repository_map = json.load(handle).get("repository_map") or {}
    except (OSError, ValueError):
        repository_map = {}
    targets = [str(t) for t in repository_map.get("target_files") or []]
    if not any(t.lstrip("./").endswith(CAUSE) for t in targets):
        problems.append(f"the Context Record does not name {CAUSE} among its target files: the "
                        "location the research found is not on record")
    if not [c for c in repository_map.get("conventions") or [] if str(c).strip()]:
        problems.append("the Context Record records no convention or constraint")
    for problem in problems:
        print(f"research record: {problem}")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1]))
