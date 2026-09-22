import unittest

from receipts.lines import format_item, format_total, receipt


class LinesTest(unittest.TestCase):
    def test_item_line(self):
        self.assertEqual(format_item("Coffee", 305), "Coffee                   3.05")

    def test_total_line(self):
        self.assertEqual(format_total(123456), "TOTAL                 1234.56")

    def test_receipt(self):
        self.assertEqual(receipt([("Tea", 250), ("Cake", 1999)], 180), [
            "Tea                      2.50",
            "Cake                    19.99",
            "TAX                      1.80",
            "TOTAL                   24.29",
        ])


if __name__ == "__main__":
    unittest.main()
