import datetime
import unittest

from billing.late_fees import LATE_FEE_CENTS, late_fee
from billing.terms import due_date
from core.timeutil.workdays import add_business_days, next_business_day
from notifications.reminders import reminder_day

D = datetime.date


class HiddenBusinessDayTest(unittest.TestCase):
    def test_a_friday_holiday_rolls_over_the_weekend_and_the_monday_holiday(self):
        self.assertEqual(next_business_day(D(2025, 4, 18)), D(2025, 4, 22))

    def test_consecutive_holidays_are_all_skipped(self):
        self.assertEqual(next_business_day(D(2025, 12, 25)), D(2025, 12, 29))

    def test_an_ordinary_day_is_itself(self):
        self.assertEqual(next_business_day(D(2025, 3, 5)), D(2025, 3, 5))
        self.assertEqual(add_business_days(D(2025, 3, 3), 5), D(2025, 3, 10))

    def test_the_reported_invoice(self):
        self.assertEqual(due_date(D(2025, 4, 4), "net10"), D(2025, 4, 22))
        self.assertEqual(late_fee(D(2025, 4, 4), D(2025, 4, 23), "net10"), 0)
        self.assertEqual(late_fee(D(2025, 4, 4), D(2025, 4, 24), "net10"), LATE_FEE_CENTS)

    def test_everything_built_on_the_due_date_moves_with_it(self):
        self.assertEqual(reminder_day(D(2025, 4, 4), "net10"), D(2025, 4, 20))
