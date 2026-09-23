import unittest

from timesheet.hours import total_minutes


class TestTotalMinutes(unittest.TestCase):
    def test_empty_timesheet_totals_zero(self):
        self.assertEqual(total_minutes([]), 0)

    def test_single_entry(self):
        self.assertEqual(total_minutes([("09:00", "10:30")]), 90)


if __name__ == "__main__":
    unittest.main()
