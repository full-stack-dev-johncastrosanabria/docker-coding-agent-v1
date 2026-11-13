import unittest

from calc.net import net


class NetTest(unittest.TestCase):
    def test_exact(self):
        self.assertEqual(net(1000, 20), 800)

    def test_discount_rounds_half_up(self):
        self.assertEqual(net(1050, 5), 997)   # discount 52.5 -> 53
