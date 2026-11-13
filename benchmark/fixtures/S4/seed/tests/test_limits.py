import unittest

from app.limits import DAILY_LIMIT, may_send


class MaySendTest(unittest.TestCase):
    def test_under_the_limit(self):
        self.assertTrue(may_send(DAILY_LIMIT - 1))

    def test_at_the_limit(self):
        self.assertFalse(may_send(DAILY_LIMIT))
