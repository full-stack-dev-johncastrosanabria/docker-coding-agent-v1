import unittest

from inventory.report import availability_report, stock_report
from inventory.stock import Stock


class HiddenReservationTest(unittest.TestCase):
    def setUp(self):
        self.stock = Stock()
        self.stock.receive("A-1", 10)
        self.stock.receive("B-2", 5)

    def test_reserve_reduces_available_not_on_hand(self):
        self.stock.reserve("o1", "A-1", 3)
        self.assertEqual(self.stock.available("A-1"), 7)
        self.assertEqual(self.stock.on_hand("A-1"), 10)

    def test_reserve_validation(self):
        for bad in (0, -2, 2.5, True):
            with self.assertRaises(ValueError):
                self.stock.reserve("o1", "A-1", bad)
        self.stock.reserve("o1", "A-1", 3)
        with self.assertRaises(ValueError):
            self.stock.reserve("o1", "B-2", 1)   # one reservation per order
        with self.assertRaises(ValueError):
            self.stock.reserve("o2", "A-1", 8)   # only 7 available
        self.stock.reserve("o2", "A-1", 7)
        self.assertEqual(self.stock.available("A-1"), 0)

    def test_release(self):
        self.stock.reserve("o1", "A-1", 4)
        self.stock.release("o1")
        self.assertEqual(self.stock.available("A-1"), 10)
        with self.assertRaises(KeyError):
            self.stock.release("o1")

    def test_fulfill_ships_the_reservation(self):
        self.stock.reserve("o1", "A-1", 4)
        self.stock.fulfill("o1")
        self.assertEqual(self.stock.on_hand("A-1"), 6)
        self.assertEqual(self.stock.available("A-1"), 6)
        with self.assertRaises(KeyError):
            self.stock.fulfill("o1")
        with self.assertRaises(KeyError):
            self.stock.fulfill("never")

    def test_ship_cannot_take_reserved_stock(self):
        self.stock.reserve("o1", "B-2", 4)
        with self.assertRaises(ValueError):
            self.stock.ship("B-2", 2)
        self.stock.ship("B-2", 1)
        self.assertEqual(self.stock.on_hand("B-2"), 4)
        self.stock.fulfill("o1")
        self.assertEqual(self.stock.on_hand("B-2"), 0)

    def test_unknown_sku_has_nothing_available(self):
        self.assertEqual(self.stock.available("Z-9"), 0)
        with self.assertRaises(ValueError):
            self.stock.reserve("o1", "Z-9", 1)

    def test_reports(self):
        self.stock.reserve("o1", "A-1", 3)
        self.assertEqual(stock_report(self.stock), ["A-1 10", "B-2 5"])
        self.assertEqual(availability_report(self.stock), ["A-1 10 7", "B-2 5 5"])


if __name__ == "__main__":
    unittest.main()
