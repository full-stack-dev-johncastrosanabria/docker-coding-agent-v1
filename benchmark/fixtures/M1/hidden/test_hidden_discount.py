"""M1 hidden tests: the per-line discount must be one rule, applied the same way everywhere."""

import unittest

from pricing.cli import render
from pricing.invoice import invoice_total
from pricing.lines import line_total


class TestLineDiscount(unittest.TestCase):
    def test_discount_reduces_the_line_total(self):
        self.assertEqual(line_total(3, 250, 10), 675)

    def test_discount_rounds_the_discount_amount_down(self):
        self.assertEqual(line_total(1, 99, 10), 90)

    def test_a_line_without_a_discount_is_unchanged(self):
        self.assertEqual(line_total(3, 250, 0), 750)
        self.assertEqual(line_total(3, 250), 750)

    def test_a_discount_outside_zero_to_one_hundred_is_rejected(self):
        for percent in (-1, 101):
            with self.subTest(percent=percent):
                with self.assertRaises(ValueError):
                    line_total(3, 250, percent)

    def test_a_full_discount_makes_the_line_free(self):
        self.assertEqual(line_total(4, 125, 100), 0)


class TestDiscountThreadsThroughEveryComponent(unittest.TestCase):
    def test_invoice_total_uses_the_discounted_line_total(self):
        self.assertEqual(invoice_total([(3, 250, 10), (1, 100)]), 775)

    def test_render_shows_discounted_lines_and_the_discounted_total(self):
        self.assertEqual(
            render([(2, 500, 50), (1, 125)]),
            "1. 2 x 500 = 500\n2. 1 x 125 = 125\ntotal = 625",
        )

    def test_the_same_lines_agree_between_the_report_and_the_renderer(self):
        lines = [(7, 311, 33), (2, 45, 5), (1, 1000)]
        rendered_total = int(render(lines).rsplit("= ", 1)[1])
        self.assertEqual(rendered_total, invoice_total(lines))


if __name__ == "__main__":
    unittest.main()
