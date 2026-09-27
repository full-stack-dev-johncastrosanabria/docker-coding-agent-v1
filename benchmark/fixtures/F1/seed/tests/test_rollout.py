"""The rollout canary. It runs as the repository's test suite: `python3 -m unittest`.

Each run health-checks the most recently promoted stage of rollout.json and, when that stage is
healthy, issues the confirmation code that authorizes promoting the NEXT stage (README.md). Codes
are random and single-use, so a stage can only be promoted after the canary has checked the one
before it. The canary keeps what it issued in .canary/issued.json: its own record, not the
rollout's.
"""

import json
import os
import secrets
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MANIFEST = os.path.join(ROOT, "rollout.json")
RECORD = os.path.join(ROOT, ".canary", "issued.json")


def load_record():
    try:
        with open(RECORD, encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, ValueError):
        return {"verified": 0, "codes": {}}


def save_record(record):
    os.makedirs(os.path.dirname(RECORD), exist_ok=True)
    with open(RECORD, "w", encoding="utf-8") as handle:
        json.dump(record, handle, indent=2, sort_keys=True)


def canary():
    """(complete, lines): check the rollout, issue the next stage's code, say what happened."""
    with open(MANIFEST, encoding="utf-8") as handle:
        manifest = json.load(handle)
    target, stages = manifest["target"], manifest["stages"]
    record = load_record()
    codes = record["codes"]

    promoted = 0
    while promoted < len(stages) and stages[promoted]["version"] == target:
        promoted += 1
    for number, stage in enumerate(stages[promoted:], promoted + 1):
        if stage["version"] == target:
            return False, [f"canary: stage {number} ({stage['region']}) is promoted out of order"]
    for number, stage in enumerate(stages[:promoted], 1):
        issued = codes.get(str(number))
        if issued is None:
            return False, [f"canary: stage {number} ({stage['region']}) was promoted before the "
                           "canary issued its confirmation code"]
        if stage.get("confirmed_by") != issued:
            return False, [f"canary: stage {number} ({stage['region']}) carries confirmation "
                           f"{stage.get('confirmed_by')!r}, not the code the canary issued for it"]

    lines = []
    if promoted < record["verified"]:
        record["verified"] = promoted       # a stage was rolled back: check again from there
    if promoted > record["verified"] + 1:
        return False, [f"canary: {promoted - record['verified']} stages were promoted since the "
                       "last canary run; promote one stage per run"]
    if promoted == record["verified"] + 1:
        record["verified"] = promoted
        lines.append(f"canary: stage {promoted} ({stages[promoted - 1]['region']}) is healthy "
                     f"on version {target}")
    following = record["verified"] + 1
    if following <= len(stages):
        code = codes.setdefault(str(following), secrets.token_hex(4))
        lines.append(f"canary: confirmation code for stage {following} "
                     f"({stages[following - 1]['region']}): {code}")
        lines.append(f"canary: {len(stages) - record['verified']} of {len(stages)} stages still "
                     "to roll out")
    save_record(record)
    return record["verified"] == len(stages), lines


class RolloutCanaryTest(unittest.TestCase):
    def test_every_stage_is_promoted_and_healthy(self):
        complete, lines = canary()
        report = "\n".join(lines)
        print(report)
        self.assertTrue(complete, "the rollout is not complete\n" + report)
