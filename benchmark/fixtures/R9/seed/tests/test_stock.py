import unittest

from inventory.report import stock_report
from inventory.stock import Stock


class StockTest(unittest.TestCase):
    def setUp(self):
        self.stock = Stock()
        self.stock.receive("B-2", 5)
        self.stock.receive("A-1", 10)

    def test_receive_and_ship(self):
        self.stock.ship("A-1", 4)
        self.assertEqual(self.stock.on_hand("A-1"), 6)

    def test_cannot_ship_more_than_on_hand(self):
        with self.assertRaises(ValueError):
            self.stock.ship("B-2", 6)

    def test_quantities_must_be_positive(self):
        for bad in (0, -1, 1.5, True):
            with self.assertRaises(ValueError):
                self.stock.receive("A-1", bad)

    def test_report(self):
        self.assertEqual(stock_report(self.stock), ["A-1 10", "B-2 5"])


if __name__ == "__main__":
    unittest.main()
