import unittest

from service.settings import load_settings, validate


class SettingsTest(unittest.TestCase):
    def test_the_shipped_configuration_is_valid(self):
        load_settings()

    def test_an_unknown_log_level_is_rejected(self):
        settings = load_settings()
        settings["log_level"] = "loud"
        with self.assertRaises(ValueError):
            validate(settings)


if __name__ == "__main__":
    unittest.main()
