import os
import unittest

from ledger.legacy import export_csv, import_csv

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class LegacyImportTest(unittest.TestCase):
    def test_a_small_ledger_round_trips(self):
        text = "date,account,amount,memo\n2019-01-02,4000,12.30,invoice\n2019-01-03,4010,-0.07,fee\n"
        self.assertEqual(export_csv(import_csv(text)), text)

    def test_legacy_ledger_round_trip(self):
        with open(os.path.join(ROOT, "data", "legacy.csv"), encoding="utf-8") as handle:
            original = handle.read()
        self.assertEqual(export_csv(import_csv(original)), original)
