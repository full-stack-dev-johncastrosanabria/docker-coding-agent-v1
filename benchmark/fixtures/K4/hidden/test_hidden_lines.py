import ast
import inspect
import unittest

from receipts import lines
from receipts.lines import format_amount, format_item, format_tax, format_total


class HiddenRefactorTest(unittest.TestCase):
    def test_format_amount(self):
        self.assertEqual(format_amount(305), "     3.05")
        self.assertEqual(format_amount(7), "     0.07")
        self.assertEqual(format_amount(123456), "  1234.56")

    def test_behavior_is_unchanged(self):
        self.assertEqual(format_item("Coffee", 305), "Coffee                   3.05")
        self.assertEqual(format_tax(0), "TAX                      0.00")
        self.assertEqual(format_total(100), "TOTAL                    1.00")

    def test_the_duplication_is_gone(self):
        tree = ast.parse(inspect.getsource(lines))
        modulo = [node for node in ast.walk(tree)
                  if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Mod)]
        self.assertLessEqual(len(modulo), 1, "amounts are still formatted in more than one place")
        for function in (format_item, format_tax, format_total):
            calls = {node.func.id for node in ast.walk(ast.parse(inspect.getsource(function)))
                     if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)}
            self.assertIn("format_amount", calls, f"{function.__name__} does not use format_amount")


if __name__ == "__main__":
    unittest.main()
