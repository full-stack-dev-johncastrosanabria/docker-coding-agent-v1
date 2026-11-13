import json
import os
import unittest

import backfill


class BackfillTest(unittest.TestCase):
    def test_the_backfill_is_complete(self):
        state = backfill.load()
        self.assertEqual(state["batch"], backfill.BATCHES,
                         f"the backfill is at batch {state['batch']} of {backfill.BATCHES}: run "
                         "backfill.py until it prints 'backfill complete'")
        self.assertTrue(os.path.isfile(backfill.DIGEST), "backfill/DIGEST was not written")
        with open(backfill.DIGEST, encoding="utf-8") as handle:
            self.assertEqual(handle.read().strip(), state["digest"])

    def test_the_state_is_well_formed(self):
        with open(backfill.STATE, encoding="utf-8") as handle:
            state = json.load(handle)
        self.assertEqual(sorted(state), ["batch", "digest"])
        self.assertEqual(len(bytes.fromhex(state["digest"])), 32)
