import datetime
import unittest

from shipping.calendar import next_pickup_day


class PickupTest(unittest.TestCase):
    def test_saturday_is_a_pickup_day_and_sunday_is_not(self):
        self.assertEqual(next_pickup_day(datetime.date(2025, 4, 19)), datetime.date(2025, 4, 19))
        self.assertEqual(next_pickup_day(datetime.date(2025, 4, 20)), datetime.date(2025, 4, 21))
