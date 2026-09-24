import unittest

from settings.values import parse_bool


class TestHiddenParseBool(unittest.TestCase):
    def test_every_accepted_spelling(self):
        for text, value in (("true", True), ("yes", True), ("1", True),
                            ("false", False), ("no", False), ("0", False)):
            with self.subTest(text=text):
                self.assertIs(parse_bool(text), value)

    def test_case_and_surrounding_whitespace_are_ignored(self):
        for text, value in (("TRUE", True), (" Yes\n", True), ("\tNo ", False), ("False", False)):
            with self.subTest(text=text):
                self.assertIs(parse_bool(text), value)

    def test_anything_else_still_raises(self):
        for text in ("", "   ", "y", "on", "2", "truthy", "n o"):
            with self.subTest(text=text), self.assertRaises(ValueError):
                parse_bool(text)


if __name__ == "__main__":
    unittest.main()
