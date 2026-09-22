import unittest

from billing.money import parse_amount


class HiddenParseAmountTest(unittest.TestCase):
    def test_one_decimal_place_is_tenths(self):
        self.assertEqual(parse_amount("0.5"), 50)
        self.assertEqual(parse_amount("1,234.5"), 123450)

    def test_two_decimal_places(self):
        self.assertEqual(parse_amount("3.07"), 307)
        self.assertEqual(parse_amount("1,234.50"), 123450)

    def test_whole_and_padded(self):
        self.assertEqual(parse_amount("7"), 700)
        self.assertEqual(parse_amount(" 12.00 "), 1200)
        self.assertEqual(parse_amount(".25"), 25)

    def test_too_many_decimals(self):
        with self.assertRaises(ValueError):
            parse_amount("1.234")


if __name__ == "__main__":
    unittest.main()
