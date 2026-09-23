import unittest

from settings.values import parse_bool, parse_port


class TestParseBool(unittest.TestCase):
    def test_true_and_false(self):
        self.assertIs(parse_bool("true"), True)
        self.assertIs(parse_bool("false"), False)

    def test_anything_else_is_rejected(self):
        for text in ("", "maybe", "2"):
            with self.subTest(text=text), self.assertRaises(ValueError):
                parse_bool(text)


class TestParsePort(unittest.TestCase):
    def test_a_valid_port(self):
        self.assertEqual(parse_port("8080"), 8080)

    def test_out_of_range_is_rejected(self):
        for text in ("0", "65536"):
            with self.subTest(text=text), self.assertRaises(ValueError):
                parse_port(text)


if __name__ == "__main__":
    unittest.main()
