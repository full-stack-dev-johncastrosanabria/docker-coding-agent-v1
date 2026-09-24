import unittest

from report.table import format_cell


class TestFormatCell(unittest.TestCase):
    def test_none_is_a_dash(self):
        self.assertEqual(format_cell(None), "-")

    def test_floats_have_two_decimals(self):
        self.assertEqual(format_cell(7.5), "7.50")

    def test_everything_else_is_str(self):
        self.assertEqual([format_cell(12), format_cell("Ann")], ["12", "Ann"])


if __name__ == "__main__":
    unittest.main()
