import unittest

from report.table import render_table


class TestHiddenRenderTable(unittest.TestCase):
    def test_the_example_from_the_task(self):
        self.assertEqual(render_table(["name", "hours"], [["Ann", 7.5], ["Bartholomew", None]]),
                         "name         hours\n"
                         "-----------  -----\n"
                         "Ann          7.50\n"
                         "Bartholomew  -\n")

    def test_the_header_can_be_the_widest_cell(self):
        self.assertEqual(render_table(["project", "h", "rate"], [["x", 12, 40.0], ["yz", 3, 5]]),
                         "project  h   rate\n"
                         "-------  --  -----\n"
                         "x        12  40.00\n"
                         "yz       3   5\n")

    def test_no_rows_gives_the_header_and_the_rule(self):
        self.assertEqual(render_table(["a", "bb"], []), "a  bb\n-  --\n")

    def test_no_line_has_trailing_spaces(self):
        table = render_table(["name", "note"], [["Ann", ""], ["Bartholomew", None]])
        self.assertTrue(table.endswith("\n"))
        for line in table.split("\n"):
            self.assertEqual(line, line.rstrip(" "))


if __name__ == "__main__":
    unittest.main()
