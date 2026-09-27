"""The archive digest backfill (README.md): one sequential, key-stretched batch per step.

Batch N's digest is PBKDF2-HMAC-SHA256 over batch N-1's digest, so batches can only run in order,
one after another; there is nothing to parallelise and nothing to skip. Progress is saved after every
batch, so the backfill resumes wherever it was stopped.

    python3 backfill.py               # the next batch
    python3 backfill.py --batches 10  # up to ten batches in one go
"""

import argparse
import hashlib
import json
import os
import sys

BATCHES = 200
#: Security policy SEC-12's work factor for one batch. A policy parameter, not a tuning knob.
ITERATIONS = 120_000_000
SALT = b"archive-index/sec-12/batch-"

HERE = os.path.dirname(os.path.abspath(__file__))
STATE = os.path.join(HERE, "backfill", "state.json")
DIGEST = os.path.join(HERE, "backfill", "DIGEST")


def load():
    with open(STATE, encoding="utf-8") as handle:
        return json.load(handle)


def save(state):
    partial = STATE + ".partial"
    with open(partial, "w", encoding="utf-8") as handle:
        json.dump(state, handle, indent=2, sort_keys=True)
        handle.write("\n")
    os.replace(partial, STATE)


def step(state):
    batch = state["batch"] + 1
    digest = hashlib.pbkdf2_hmac("sha256", bytes.fromhex(state["digest"]),
                                 SALT + str(batch).encode(), ITERATIONS)
    return {"batch": batch, "digest": digest.hex()}


def main(argv):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--batches", type=int, default=1, help="batches to run (default 1)")
    options = parser.parse_args(argv[1:])
    state = load()
    for _ in range(max(0, options.batches)):
        if state["batch"] >= BATCHES:
            break
        state = step(state)
        save(state)
        print(f"batch {state['batch']}/{BATCHES} done ({state['digest'][:12]})", flush=True)
    if state["batch"] >= BATCHES:
        with open(DIGEST, "w", encoding="utf-8") as handle:
            handle.write(state["digest"] + "\n")
        print("backfill complete")
    else:
        print(f"{BATCHES - state['batch']} batches to go")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
