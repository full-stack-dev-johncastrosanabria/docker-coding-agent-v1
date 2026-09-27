import unittest

from ledger.display import format_amount
from ledger.limits import MAX_CENTS, MIN_CENTS

RECEIPT_COLUMN = 12


class DisplayTest(unittest.TestCase):
    def test_every_accepted_amount_fits_the_receipt_column(self):
        for cents in (MIN_CENTS, 1, 99, 12_345, 100_000, 123_456_789, MAX_CENTS):
            with self.subTest(cents=cents):
                self.assertLessEqual(len(format_amount(cents)), RECEIPT_COLUMN)

    def test_amounts_keep_two_decimals(self):
        self.assertTrue(format_amount(12_345).endswith("123.45"))
        self.assertTrue(format_amount(100).endswith("1.00"))

    def test_amounts_outside_the_accepted_range_are_refused(self):
        for cents in (MIN_CENTS - 1, MAX_CENTS + 1):
            with self.subTest(cents=cents):
                with self.assertRaises(ValueError):
                    format_amount(cents)
