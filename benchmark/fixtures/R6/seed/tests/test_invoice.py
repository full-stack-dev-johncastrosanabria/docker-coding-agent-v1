import unittest

from billing.invoice import invoice_total


class InvoiceTotalTest(unittest.TestCase):
    def test_whole_amounts(self):
        self.assertEqual(invoice_total([("book", "12", 2)]), 2400)

    def test_mixed_amounts(self):
        lines = [("pen", "1.5", 2), ("book", "12.25", 1), ("desk", "1,000", 1)]
        self.assertEqual(invoice_total(lines), 300 + 1225 + 100000)


if __name__ == "__main__":
    unittest.main()
