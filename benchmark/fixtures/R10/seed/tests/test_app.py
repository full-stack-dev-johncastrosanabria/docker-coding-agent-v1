import datetime
import unittest

from app.events import event_days
from app.export import export_rows
from app.reports import by_month, pages


class AppTest(unittest.TestCase):
    def test_event_days(self):
        events = [{"title": "a", "date": "2024-03-01"}, {"title": "b", "date": "2024-01-15"},
                  {"title": "c", "date": "2024-03-01"}]
        self.assertEqual(event_days(events),
                         [datetime.date(2024, 1, 15), datetime.date(2024, 3, 1)])

    def test_by_month(self):
        rows = [{"date": "2024-02-29"}, {"date": "2024-02-01"}, {"date": "2023-12-31"}]
        self.assertEqual(by_month(rows), {(2024, 2): 2, (2023, 12): 1})

    def test_export(self):
        rows = [{"date": "2024-05-02", "amount": 3}, {"date": "2024-05-01", "amount": 7}]
        self.assertEqual(export_rows(rows), ["2024-05-01,7", "2024-05-02,3"])

    def test_pages(self):
        self.assertEqual(pages(list(range(5)), 2), [[0, 1], [2, 3], [4]])


if __name__ == "__main__":
    unittest.main()
