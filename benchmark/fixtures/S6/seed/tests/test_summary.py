import os
import unittest

from report.summary import summarize

DATA = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "sales.csv")


class SummarizeTest(unittest.TestCase):
    def test_totals_per_region_sorted(self):
        self.assertEqual(summarize(DATA), "region,total\neast,45\nnorth,300\nsouth,100\n")
