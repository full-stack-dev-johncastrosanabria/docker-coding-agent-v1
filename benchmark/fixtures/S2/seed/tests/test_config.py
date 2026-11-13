import unittest

from service import config


class ConfigTest(unittest.TestCase):
    def test_timeouts_are_positive(self):
        self.assertGreater(config.TIMEOUT_SECONDS, 0)
        self.assertGreaterEqual(config.RETRIES, 0)
