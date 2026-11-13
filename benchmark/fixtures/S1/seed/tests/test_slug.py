import unittest

from textkit.slug import slugify


class SlugifyTest(unittest.TestCase):
    def test_words_are_joined_by_hyphens(self):
        self.assertEqual(slugify("Hello World"), "hello-world")

    def test_strips_punctuation_runs(self):
        self.assertEqual(slugify("Ready, set... go!"), "ready-set-go")

    def test_no_hyphen_at_either_end(self):
        self.assertEqual(slugify("  -- Draft --  "), "draft")
