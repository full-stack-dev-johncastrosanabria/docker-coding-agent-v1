"""Acceptance tests for thousands grouping on receipts (product request)."""

import unittest

from ledger.display import format_amount
from ledger.limits import MAX_CENTS


class GroupingTest(unittest.TestCase):
    def test_thousands_are_grouped_for_every_accepted_amount(self):
        cases = {
            0: "0.00",
            12_345: "123.45",
            100_000: "1,000.00",
            123_456_789: "1,234,567.89",
            MAX_CENTS: "999,999,999.99",
        }
        for cents, expected in cases.items():
            with self.subTest(cents=cents):
                self.assertEqual(format_amount(cents), expected)
