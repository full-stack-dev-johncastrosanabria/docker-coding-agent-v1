import unittest

from mathutil.clamp import clamp


class ClampTest(unittest.TestCase):
    def test_inside_the_range_is_unchanged(self):
        self.assertEqual(clamp(5, 0, 10), 5)

    def test_below_and_above(self):
        self.assertEqual(clamp(-3, 0, 10), 0)
        self.assertEqual(clamp(42, 0, 10), 10)

    def test_an_inverted_range_is_rejected(self):
        with self.assertRaises(ValueError):
            clamp(1, 10, 0)


if __name__ == "__main__":
    unittest.main()
