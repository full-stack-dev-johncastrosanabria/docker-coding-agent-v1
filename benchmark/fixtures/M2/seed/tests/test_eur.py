import unittest

from billing.eur import format_eur


class TestFormatEur(unittest.TestCase):
    def test_euros_and_cents(self):
        self.assertEqual(format_eur(1234), "12,34 EUR")

    def test_a_larger_amount(self):
        self.assertEqual(format_eur(45012), "450,12 EUR")


if __name__ == "__main__":
    unittest.main()
