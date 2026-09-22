import unittest

from mathutil.clamp import clamp


class HiddenClampTest(unittest.TestCase):
    def test_bounds_are_inclusive(self):
        self.assertEqual(clamp(0, 0, 10), 0)
        self.assertEqual(clamp(10, 0, 10), 10)

    def test_a_single_point_range(self):
        self.assertEqual(clamp(-1, 3, 3), 3)
        self.assertEqual(clamp(9, 3, 3), 3)

    def test_floats(self):
        self.assertEqual(clamp(0.25, 0.0, 1.0), 0.25)
        self.assertEqual(clamp(1.5, 0.0, 1.0), 1.0)

    def test_inverted_range_even_when_value_is_inside(self):
        with self.assertRaises(ValueError):
            clamp(5, 6, 4)


if __name__ == "__main__":
    unittest.main()
