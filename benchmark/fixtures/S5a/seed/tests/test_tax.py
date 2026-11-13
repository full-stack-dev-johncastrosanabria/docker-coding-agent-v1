import unittest

from calc.tax import vat


class VatTest(unittest.TestCase):
    def test_exact(self):
        self.assertEqual(vat(1000, 20), 200)

    def test_rounds_half_up(self):
        self.assertEqual(vat(1050, 5), 53)   # 52.5 -> 53
