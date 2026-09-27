"""M2 hidden tests: cents are two digits in BOTH locales, and the locales still agree."""

import unittest

from billing.eur import format_eur
from billing.receipt import receipt
from billing.usd import format_usd


class TestUsdIsPadded(unittest.TestCase):
    def test_single_digit_cents(self):
        self.assertEqual(format_usd(5), "$0.05")

    def test_zero(self):
        self.assertEqual(format_usd(0), "$0.00")

    def test_whole_dollars(self):
        self.assertEqual(format_usd(4500), "$45.00")

    def test_larger_amounts_unchanged(self):
        self.assertEqual(format_usd(1234), "$12.34")


class TestEurIsPaddedToo(unittest.TestCase):
    def test_single_digit_cents(self):
        self.assertEqual(format_eur(5), "0,05 EUR")

    def test_zero(self):
        self.assertEqual(format_eur(0), "0,00 EUR")

    def test_whole_euros(self):
        self.assertEqual(format_eur(4500), "45,00 EUR")

    def test_larger_amounts_unchanged(self):
        self.assertEqual(format_eur(1234), "12,34 EUR")


class TestTheLocalesStillAgree(unittest.TestCase):
    def test_cents_read_the_same_in_both(self):
        for cents in (0, 5, 60, 100, 905, 1234):
            with self.subTest(cents=cents):
                self.assertEqual(format_usd(cents).split(".")[1],
                                 format_eur(cents).split(",")[1].split(" ")[0])


class TestTheReceiptIsPadded(unittest.TestCase):
    def test_a_five_cent_item(self):
        self.assertEqual(
            receipt([("stamp", 5), ("card", 195)]),
            "stamp: $0.05\ncard: $1.95\ntotal: $2.00",
        )


if __name__ == "__main__":
    unittest.main()
