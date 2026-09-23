import configparser
import os
import unittest

from app import config


class TestHiddenDefaults(unittest.TestCase):
    def test_the_requested_defaults(self):
        settings = config.load()
        self.assertEqual(settings["port"], 8443)
        self.assertIs(settings["cache_enabled"], True)
        self.assertEqual(settings["cache_ttl_seconds"], 300)

    def test_every_other_default_is_unchanged(self):
        settings = config.load()
        self.assertEqual(settings["host"], "127.0.0.1")
        self.assertEqual(settings["workers"], 2)
        self.assertEqual(settings["log_level"], "info")

    def test_the_file_keeps_its_sections_and_keys(self):
        parser = configparser.ConfigParser()
        parser.read(os.path.join(os.path.dirname(config.__file__), "defaults.ini"),
                    encoding="utf-8")
        self.assertEqual({section: sorted(parser[section]) for section in parser.sections()},
                         {"server": ["host", "port", "workers"],
                          "cache": ["enabled", "ttl_seconds"],
                          "logging": ["level"]})


if __name__ == "__main__":
    unittest.main()
