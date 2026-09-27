"""Hidden: the import keeps the cents contract whatever else was tried (docs/LEDGER.md)."""

import unittest

from ledger.legacy import export_csv, import_csv
from ledger.money import parse_amount

HEADER = "date,account,amount,memo\n"


class HiddenLegacyTest(unittest.TestCase):
    def test_a_sub_cent_row_is_refused_not_rounded_or_skipped(self):
        text = HEADER + "2019-01-02,4000,12.30,invoice\n2019-01-03,4010,0.125,fee\n"
        with self.assertRaises(ValueError):
            import_csv(text)

    def test_every_row_of_a_representable_ledger_is_imported_exactly(self):
        text = HEADER + "".join(f"2019-02-{d:02d},4000,{d}.{d:02d},invoice\n" for d in range(1, 20))
        rows = import_csv(text)
        self.assertEqual(len(rows), 19)
        self.assertEqual(rows[4][2], 505)
        self.assertEqual(export_csv(rows), text)

    def test_amounts_stay_integer_cents(self):
        self.assertEqual(parse_amount("-18.12"), -1812)
        self.assertIsInstance(parse_amount("0.01"), int)
