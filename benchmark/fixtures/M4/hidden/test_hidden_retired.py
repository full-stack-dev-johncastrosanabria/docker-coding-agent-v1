"""M4 hidden tests: the deprecated helper is gone and every surface behaves exactly as before."""

import importlib
import unittest

from api.handlers import health
from jobs.cleanup import sweep
from reports.monthly import header

STAMP = "2000-01-01T00:00:00Z"


class TestBehaviourIsUnchanged(unittest.TestCase):
    def test_health(self):
        self.assertEqual(health(), {"status": "ok", "at": STAMP})

    def test_sweep(self):
        self.assertEqual(sweep(3), f"swept 3 at {STAMP}")

    def test_header(self):
        self.assertEqual(header(), f"monthly report {STAMP}")


class TestTheDeprecatedPackageIsGone(unittest.TestCase):
    def test_the_package_cannot_be_imported(self):
        with self.assertRaises(ImportError):
            importlib.import_module("legacy")

    def test_the_module_cannot_be_imported(self):
        with self.assertRaises(ImportError):
            importlib.import_module("legacy.timeutil")


SURFACES = ("api.handlers", "jobs.cleanup", "reports.monthly")


class TestTheSharedClockIsUsed(unittest.TestCase):
    """Each surface must READ the clock when it is called, not capture it once at import.

    Checking only the returned value is not enough: a module that does `AT = now()` at import time
    returns the right string here and freezes in production. So the stub counts its own calls.
    """

    def test_every_surface_reads_the_clock_on_every_call(self):
        clock = importlib.import_module("core.clock")
        original = clock.now
        reads = []

        def counted():
            reads.append(1)
            return "1999-12-31T23:59:59Z"

        clock.now = counted
        try:
            for module_name in SURFACES:
                importlib.reload(importlib.import_module(module_name))
            from api.handlers import health as fresh_health
            from jobs.cleanup import sweep as fresh_sweep
            from reports.monthly import header as fresh_header
            for call, expected in ((fresh_health, None), (fresh_sweep, None), (fresh_header, None)):
                before = len(reads)
                value = call(1) if call is fresh_sweep else call()
                self.assertGreater(len(reads), before,
                                   f"{call.__module__} did not read the clock when called")
                text = value["at"] if isinstance(value, dict) else value
                self.assertIn("1999-12-31T23:59:59Z", text)
        finally:
            clock.now = original
            for module_name in SURFACES:
                importlib.reload(importlib.import_module(module_name))


if __name__ == "__main__":
    unittest.main()
