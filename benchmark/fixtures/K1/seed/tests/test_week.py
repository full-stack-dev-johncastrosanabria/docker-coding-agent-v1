import unittest

from timesheet.week import week_label


class TestWeekLabel(unittest.TestCase):
    def test_mid_year(self):
        self.assertEqual(week_label("2024-01-17"), "2024-W03")

    def test_year_boundary_uses_iso_week_year(self):
        # 2024-12-30 is a Monday in ISO week 1 of 2025.
        self.assertEqual(week_label("2024-12-30"), "2025-W01")


if __name__ == "__main__":
    unittest.main()
