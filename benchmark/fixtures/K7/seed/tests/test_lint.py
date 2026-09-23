import unittest

from checks.lint import lint
from checks.registry import Finding


class TestLint(unittest.TestCase):
    def test_clean_text_has_no_findings(self):
        self.assertEqual(lint("def f():\n    return 1\n"), [])

    def test_trailing_whitespace(self):
        self.assertEqual(lint("x = 1 \n"), [Finding("L001", 1, "trailing whitespace")])

    def test_tab_indentation(self):
        self.assertEqual(lint("if x:\n\tpass\n"), [Finding("L002", 2, "tab used for indentation")])

    def test_findings_are_ordered_by_line_then_code(self):
        self.assertEqual([(f.line, f.code) for f in lint("\tx = 1 \ny = 2 \n")],
                         [(1, "L001"), (1, "L002"), (2, "L001")])


if __name__ == "__main__":
    unittest.main()
