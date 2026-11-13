import unittest

from textkit.slug import slugify


class HiddenSlugifyTest(unittest.TestCase):
    def test_mixed_runs_collapse_to_one_hyphen(self):
        self.assertEqual(slugify("C++ & Rust: 2024 -- notes"), "c-rust-2024-notes")

    def test_non_ascii_is_dropped_without_leaving_hyphen_runs(self):
        self.assertEqual(slugify("Café — résumé"), "caf-rsum")

    def test_only_punctuation_is_empty(self):
        self.assertEqual(slugify("!!! ???"), "")
