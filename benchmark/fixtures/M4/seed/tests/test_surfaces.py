import unittest

from api.handlers import health
from jobs.cleanup import sweep
from reports.monthly import header


class TestSurfaces(unittest.TestCase):
    def test_health_reports_a_timestamp(self):
        self.assertEqual(health(), {"status": "ok", "at": "2000-01-01T00:00:00Z"})

    def test_sweep_reports_a_timestamp(self):
        self.assertEqual(sweep(3), "swept 3 at 2000-01-01T00:00:00Z")

    def test_report_header_has_a_timestamp(self):
        self.assertEqual(header(), "monthly report 2000-01-01T00:00:00Z")


if __name__ == "__main__":
    unittest.main()
