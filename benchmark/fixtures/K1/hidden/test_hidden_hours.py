import unittest

from timesheet.hours import total_minutes


class TestHiddenTotalMinutes(unittest.TestCase):
    def test_every_entry_counts(self):
        self.assertEqual(total_minutes([("09:00", "10:30"), ("11:00", "12:15"),
                                        ("13:00", "17:00")]), 90 + 75 + 240)

    def test_entry_order_does_not_matter(self):
        self.assertEqual(total_minutes([("13:00", "14:00"), ("08:30", "09:00")]), 90)

    def test_empty_timesheet_still_totals_zero(self):
        self.assertEqual(total_minutes([]), 0)


if __name__ == "__main__":
    unittest.main()
