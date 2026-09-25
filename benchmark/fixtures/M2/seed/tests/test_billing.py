import unittest

from billing.money import cents_to_text
from billing.receipt import receipt
from billing.summary import monthly_summary


class TestBilling(unittest.TestCase):
    def test_whole_cents_format(self):
        self.assertEqual(cents_to_text(1234), "$12.34")

    def test_receipt_lists_items_then_the_total(self):
        self.assertEqual(
            receipt([("tea", 450), ("mug", 1275)]),
            "tea: $4.50\nmug: $12.75\ntotal: $17.25",
        )

    def test_summary_lists_months(self):
        self.assertEqual(monthly_summary([("jan", 1050)]), "jan 10.50")


if __name__ == "__main__":
    unittest.main()
