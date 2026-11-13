import datetime
import unittest

from billing.late_fees import LATE_FEE_CENTS, late_fee
from billing.terms import due_date

D = datetime.date


class LateFeeTest(unittest.TestCase):
    def test_due_date_skips_the_weekend(self):
        self.assertEqual(due_date(D(2025, 3, 3), "net5"), D(2025, 3, 10))

    def test_paid_on_the_due_date_has_no_fee(self):
        self.assertEqual(late_fee(D(2025, 3, 3), D(2025, 3, 10), "net5"), 0)

    def test_paid_a_week_late_has_the_fee(self):
        self.assertEqual(late_fee(D(2025, 3, 3), D(2025, 3, 18), "net5"), LATE_FEE_CENTS)
