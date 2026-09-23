import json
import os
import unittest

from service.settings import CONFIG_PATH, load_settings

SEED = {"name": "orders", "port": 8080, "timeout_seconds": 30, "log_level": "info",
        "features": {"audit": False, "metrics": True}}


class HiddenConfigChangeTest(unittest.TestCase):
    def test_the_requested_values(self):
        settings = load_settings()
        self.assertEqual(settings["timeout_seconds"], 45)
        self.assertEqual(settings["log_level"], "warning")
        self.assertIs(settings["features"]["audit"], True)

    def test_nothing_else_changed(self):
        with open(CONFIG_PATH, encoding="utf-8") as handle:
            settings = json.load(handle)
        expected = json.loads(json.dumps(SEED))
        expected["timeout_seconds"] = 45
        expected["log_level"] = "warning"
        expected["features"]["audit"] = True
        self.assertEqual(settings, expected)


if __name__ == "__main__":
    unittest.main()
