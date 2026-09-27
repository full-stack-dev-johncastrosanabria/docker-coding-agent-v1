import unittest
from decimal import Decimal

from billing.fees import LATE_FEE_RATES, late_fee


class LateFeeTest(unittest.TestCase):
    def test_every_tier_has_a_monthly_rate_below_five_percent(self):
        for tier, rate in LATE_FEE_RATES.items():
            with self.subTest(tier=tier):
                self.assertGreater(rate, Decimal("0"))
                self.assertLess(rate, Decimal("0.05"))

    def test_a_fee_is_the_rate_times_the_overdue_amount_per_month(self):
        self.assertEqual(late_fee("standard", "1000.00", 2), Decimal("30.00"))

    def test_a_fee_is_rounded_to_the_cent_half_up(self):
        self.assertEqual(late_fee("silver", "10.05", 1), Decimal("0.12"))

    def test_no_months_overdue_is_no_fee(self):
        self.assertEqual(late_fee("gold", "500.00", 0), Decimal("0.00"))

    def test_negative_months_are_refused(self):
        with self.assertRaises(ValueError):
            late_fee("gold", "500.00", -1)
