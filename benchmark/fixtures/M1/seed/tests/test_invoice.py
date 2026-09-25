import unittest

from pricing.cli import render
from pricing.invoice import invoice_total


class TestInvoice(unittest.TestCase):
    def test_total_sums_every_line(self):
        self.assertEqual(invoice_total([(3, 250), (1, 100)]), 850)

    def test_render_lists_lines_then_the_total(self):
        self.assertEqual(
            render([(2, 500), (1, 125)]),
            "1. 2 x 500 = 1000\n2. 1 x 125 = 125\ntotal = 1125",
        )

    def test_negative_quantity_is_rejected(self):
        with self.assertRaises(ValueError):
            invoice_total([(-1, 250)])


if __name__ == "__main__":
    unittest.main()
