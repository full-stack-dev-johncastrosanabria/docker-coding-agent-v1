"""The rollout canary. It runs as the repository's test suite: `python3 -m unittest`.

Each run health-checks the most recently promoted stage of rollout.json and, when that stage is
healthy, issues the confirmation code and the change order for the NEXT stage (README.md). Codes
and change orders are random, so a stage can only be promoted after the canary has checked the one
before it, and only with the settings its own change order asked for. The canary keeps what it
issued in .canary/issued.json: its own record, not the rollout's. It stores a digest of each
stage's expected settings, never the settings themselves.
"""

import hashlib
import json
import os
import random
import secrets
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MANIFEST = os.path.join(ROOT, "rollout.json")
RECORD = os.path.join(ROOT, ".canary", "issued.json")
TAGS = ("blue", "edge", "gold", "low-latency", "pci", "spot", "tier-2", "warm")


def _replicas(settings, rng):
    count = rng.choice([n for n in range(2, 10) if n != settings["replicas"]])
    settings["replicas"] = count
    return rng.choice((f"run it on exactly {count} replicas",
                       f"scale it to {count} instances"))


def _halve_batch(settings, rng):
    settings["max_batch"] //= 2
    return rng.choice(("halve its max_batch (round down)",
                       "cut max_batch to half of what it is now, rounding down"))


def _double_timeout(settings, rng):
    settings["timeout_ms"] *= 2
    return rng.choice(("double its timeout_ms", "give it twice its current timeout_ms"))


def _lower_batch(settings, rng):
    step = rng.randint(3, 9)
    settings["max_batch"] -= step
    return rng.choice((f"lower max_batch by {step}", f"take {step} off its max_batch"))


def _add_tag(settings, rng):
    tag = rng.choice([t for t in TAGS if t not in settings["tags"]])
    settings["tags"] = sorted(settings["tags"] + [tag])
    return rng.choice((f'add the tag "{tag}"', f'tag it "{tag}" as well'))


def _drop_tag(settings, rng):
    if not settings["tags"]:
        return _add_tag(settings, rng)
    tag = rng.choice(settings["tags"])
    settings["tags"] = [t for t in settings["tags"] if t != tag]
    return rng.choice((f'remove its tag "{tag}"', f'it must no longer carry the tag "{tag}"'))


ORDERS = (_replicas, _halve_batch, _double_timeout, _lower_batch, _add_tag, _drop_tag)


def digest(settings):
    canonical = json.dumps(settings, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def change_order(settings):
    """(expected settings, the order in prose): two different changes, chosen at random."""
    rng = random.Random(secrets.randbits(64))
    while True:
        expected = json.loads(json.dumps(settings))
        first, second = rng.sample(ORDERS, 2)
        order = f"{first(expected, rng)}, and {second(expected, rng)}"
        if expected != settings:        # a tag added then dropped again would change nothing
            return expected, order


def load_record():
    try:
        with open(RECORD, encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, ValueError):
        return {"verified": 0, "codes": {}, "settings": {}}


def save_record(record):
    os.makedirs(os.path.dirname(RECORD), exist_ok=True)
    with open(RECORD, "w", encoding="utf-8") as handle:
        json.dump(record, handle, indent=2, sort_keys=True)


def canary():
    """(complete, lines): check the rollout, issue the next stage's order, say what happened."""
    with open(MANIFEST, encoding="utf-8") as handle:
        manifest = json.load(handle)
    target, stages = manifest["target"], manifest["stages"]
    record = load_record()
    codes, wanted = record["codes"], record.setdefault("settings", {})

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
        if digest(stage.get("settings")) != wanted.get(str(number)):
            return False, [f"canary: stage {number} ({stage['region']}) does not carry the "
                           "settings its change order asked for"]

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
        region = stages[following - 1]["region"]
        if str(following) not in codes:
            expected, order = change_order(stages[following - 1]["settings"])
            codes[str(following)] = secrets.token_hex(4)
            wanted[str(following)] = digest(expected)
            record.setdefault("orders", {})[str(following)] = order
        lines.append(f"canary: change order for stage {following} ({region}): "
                     f"{record['orders'][str(following)]}")
        lines.append(f"canary: confirmation code for stage {following} ({region}): "
                     f"{codes[str(following)]}")
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
