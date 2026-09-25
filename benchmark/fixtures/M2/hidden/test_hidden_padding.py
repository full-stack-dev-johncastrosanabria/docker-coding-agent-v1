"""M2 hidden tests: cents are always two digits, everywhere money is shown."""

import unittest

from billing.money import cents_to_text
from billing.receipt import receipt
from billing.summary import monthly_summary


class TestCentsAreAlwaysTwoDigits(unittest.TestCase):
    def test_single_digit_cents_are_padded(self):
        self.assertEqual(cents_to_text(5), "$0.05")

    def test_zero_is_two_zeros(self):
        self.assertEqual(cents_to_text(0), "$0.00")

    def test_exact_dollars_keep_two_digits(self):
        self.assertEqual(cents_to_text(100), "$1.00")

    def test_larger_amounts_are_unchanged(self):
        self.assertEqual(cents_to_text(1234), "$12.34")


class TestEverySurfaceAgrees(unittest.TestCase):
    def test_the_receipt_pads_items_and_the_total(self):
        self.assertEqual(
            receipt([("stamp", 5), ("card", 195)]),
            "stamp: $0.05\ncard: $1.95\ntotal: $2.00",
        )

    def test_the_monthly_summary_pads_too(self):
        self.assertEqual(monthly_summary([("jan", 5)]), "jan 0.05")

    def test_the_summary_matches_the_shared_formatter(self):
        for cents in (0, 5, 60, 100, 905, 1234):
            with self.subTest(cents=cents):
                self.assertEqual(
                    monthly_summary([("m", cents)]),
                    "m " + cents_to_text(cents).lstrip("$"),
                )


if __name__ == "__main__":
    unittest.main()
