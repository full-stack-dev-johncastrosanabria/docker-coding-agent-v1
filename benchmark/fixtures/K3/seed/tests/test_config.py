import os
import tempfile
import unittest

from app import config


class TestConfig(unittest.TestCase):
    def write(self, text):
        handle = tempfile.NamedTemporaryFile("w", suffix=".ini", delete=False, encoding="utf-8")
        with handle:
            handle.write(text)
        self.addCleanup(os.remove, handle.name)
        return handle.name

    def settings(self, **overrides):
        values = {"host": "127.0.0.1", "port": "8080", "workers": "2", "enabled": "false",
                  "ttl_seconds": "60", "level": "info"}
        values.update(overrides)
        return self.write(
            "[server]\nhost = {host}\nport = {port}\nworkers = {workers}\n"
            "[cache]\nenabled = {enabled}\nttl_seconds = {ttl_seconds}\n"
            "[logging]\nlevel = {level}\n".format(**values))

    def test_the_shipped_defaults_load(self):
        self.assertIsInstance(config.load(), dict)

    def test_a_valid_file_loads_with_typed_values(self):
        settings = config.load(self.settings(enabled="yes", ttl_seconds="30"))
        self.assertIs(settings["cache_enabled"], True)
        self.assertEqual(settings["cache_ttl_seconds"], 30)

    def test_invalid_values_are_rejected(self):
        for field, value in (("port", "70000"), ("workers", "0"), ("ttl_seconds", "0"),
                             ("level", "loud")):
            with self.subTest(field=field), self.assertRaises(ValueError):
                config.load(self.settings(**{field: value}))


if __name__ == "__main__":
    unittest.main()
