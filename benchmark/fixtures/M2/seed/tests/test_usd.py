import unittest

from billing.receipt import receipt
from billing.usd import format_usd


class TestFormatUsd(unittest.TestCase):
    def test_dollars_and_cents(self):
        self.assertEqual(format_usd(1234), "$12.34")

    def test_receipt_lists_items_then_the_total(self):
        self.assertEqual(
            receipt([("tea", 450), ("mug", 1275)]),
            "tea: $4.50\nmug: $12.75\ntotal: $17.25",
        )


if __name__ == "__main__":
    unittest.main()
