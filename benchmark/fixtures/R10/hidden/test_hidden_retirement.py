import os
import unittest

import app.util
from app.events import event_days
from app.export import export_rows
from app.reports import by_month

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class HiddenRetirementTest(unittest.TestCase):
    def test_the_legacy_function_is_gone(self):
        self.assertFalse(hasattr(app.util, "legacy_parse_date"))

    def test_no_reference_remains(self):
        for current, _, files in os.walk(os.path.join(ROOT, "app")):
            for name in files:
                if name.endswith(".py"):
                    with open(os.path.join(current, name), encoding="utf-8") as handle:
                        self.assertNotIn("legacy_parse_date", handle.read(), name)

    def test_callers_now_reject_non_iso_dates(self):
        with self.assertRaises(ValueError):
            event_days([{"title": "a", "date": "01/03/2024"}])
        with self.assertRaises(ValueError):
            by_month([{"date": "29/02/2024"}])
        with self.assertRaises(ValueError):
            export_rows([{"date": "02/05/2024", "amount": 1}])

    def test_iso_behavior_is_unchanged(self):
        self.assertEqual(export_rows([{"date": " 2024-05-01 ", "amount": 7}]), ["2024-05-01,7"])

    def test_other_helpers_survive(self):
        self.assertEqual(app.util.chunks([1, 2, 3], 2), [[1, 2], [3]])


if __name__ == "__main__":
    unittest.main()
