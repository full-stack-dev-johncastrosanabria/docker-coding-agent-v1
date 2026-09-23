import unittest

from shop.pricing import apply_discount


class HiddenApplyDiscountTest(unittest.TestCase):
    def test_exact_half_cent_rounds_up(self):
        self.assertEqual(apply_discount(1, 50), 1)       # 0.5
        self.assertEqual(apply_discount(250, 1), 248)    # 247.5
        self.assertEqual(apply_discount(5, 10), 5)       # 4.5
        self.assertEqual(apply_discount(1001, 50), 501)  # 500.5

    def test_below_half_rounds_down(self):
        self.assertEqual(apply_discount(3, 33), 2)       # 2.01
        self.assertEqual(apply_discount(999, 10), 899)   # 899.1

    def test_large_amounts_are_exact(self):
        self.assertEqual(apply_discount(100000, 7), 93000)
        self.assertEqual(apply_discount(123456789, 12), 108641974)  # 108641974.32

    def test_result_is_an_integer(self):
        self.assertIsInstance(apply_discount(999, 15), int)

    def test_bounds_are_inclusive(self):
        self.assertEqual(apply_discount(700, 0), 700)
        self.assertEqual(apply_discount(700, 100), 0)
        with self.assertRaises(ValueError):
            apply_discount(700, 100.5)


if __name__ == "__main__":
    unittest.main()
