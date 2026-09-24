import unittest

from splitter.amounts import split_amount


class TestSplitAmount(unittest.TestCase):
    def test_an_even_split(self):
        self.assertEqual(split_amount(90, 3), [30, 30, 30])

    def test_the_parts_always_add_up_to_the_total(self):
        for total, parts in ((100, 3), (7, 4), (1, 5), (0, 2)):
            with self.subTest(total=total, parts=parts):
                result = split_amount(total, parts)
                self.assertEqual(len(result), parts)
                self.assertEqual(sum(result), total)

    def test_invalid_arguments_are_rejected(self):
        for total, parts in ((10, 0), (-1, 2)):
            with self.subTest(total=total, parts=parts), self.assertRaises(ValueError):
                split_amount(total, parts)


if __name__ == "__main__":
    unittest.main()
