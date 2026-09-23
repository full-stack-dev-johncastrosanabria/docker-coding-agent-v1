import unittest

from shop.pricing import apply_discount


class ApplyDiscountTest(unittest.TestCase):
    def test_no_discount_keeps_the_price(self):
        self.assertEqual(apply_discount(1234, 0), 1234)

    def test_full_discount_is_free(self):
        self.assertEqual(apply_discount(1234, 100), 0)

    def test_rounds_half_up_to_whole_cents(self):
        # 999 * 0.85 = 849.15
        self.assertEqual(apply_discount(999, 15), 849)

    def test_percent_out_of_range_is_rejected(self):
        with self.assertRaises(ValueError):
            apply_discount(100, 101)
        with self.assertRaises(ValueError):
            apply_discount(100, -1)


if __name__ == "__main__":
    unittest.main()
