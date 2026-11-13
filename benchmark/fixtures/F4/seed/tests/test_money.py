import unittest

from ledger.money import format_amount, parse_amount


class MoneyTest(unittest.TestCase):
    def test_amounts_are_integer_cents(self):
        self.assertEqual(parse_amount("1234.50"), 123450)
        self.assertEqual(parse_amount("-0.07"), -7)

    def test_formatting_round_trips(self):
        for text in ("0.00", "12.34", "-18.12", "250000.99"):
            with self.subTest(text=text):
                self.assertEqual(format_amount(parse_amount(text)), text)

    def test_a_sub_cent_amount_is_refused_never_rounded(self):
        for text in ("1.005", "0.125", "-3.999"):
            with self.subTest(text=text):
                with self.assertRaises(ValueError):
                    parse_amount(text)
