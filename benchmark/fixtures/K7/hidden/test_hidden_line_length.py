import unittest

from checks.lint import lint
from checks.registry import RULES, Finding


class TestHiddenLineLength(unittest.TestCase):
    def test_the_rule_is_registered_as_the_next_code_in_the_rules_module(self):
        self.assertEqual(sorted(RULES), ["L001", "L002", "L003"])
        self.assertEqual(RULES["L003"].__module__, "checks.rules")

    def test_a_long_line_is_reported(self):
        text = "a = 1\n" + "b" * 101 + "\n"
        self.assertEqual(lint(text), [Finding("L003", 2, "line too long (101 > 100)")])

    def test_findings_are_finding_instances(self):
        self.assertTrue(all(type(finding) is Finding for finding in lint("c" * 150)))

    def test_exactly_100_characters_is_fine(self):
        self.assertEqual(lint("d" * 100 + "\n"), [])

    def test_it_is_ordered_with_the_other_rules(self):
        text = "\t" + "e" * 104 + " \n"
        self.assertEqual([f.code for f in lint(text)], ["L001", "L002", "L003"])
        self.assertEqual(lint(text)[2].message, "line too long (106 > 100)")


if __name__ == "__main__":
    unittest.main()
