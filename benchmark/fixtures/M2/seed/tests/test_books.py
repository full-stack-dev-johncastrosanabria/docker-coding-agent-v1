"""Ledger reconciliation.

Finance reconciles the two locales against each other, so the invariant that both render an amount's
cents the same way is checked here, beside the ledger arithmetic it protects.
"""

import unittest

from billing.books import is_balanced, net
from billing.eur import format_eur
from billing.usd import format_usd


class TestLedger(unittest.TestCase):
    def test_net_subtracts_credits_from_charges(self):
        self.assertEqual(net([1000, 250], [300]), 950)

    def test_balanced_books_net_to_zero(self):
        self.assertTrue(is_balanced([500, 500], [1000]))

    def test_unbalanced_books(self):
        self.assertFalse(is_balanced([500], [100]))


class TestLocaleReconciliation(unittest.TestCase):
    def test_both_locales_render_cents_the_same(self):
        for cents in (0, 5, 60, 100, 905, 1234):
            with self.subTest(cents=cents):
                self.assertEqual(
                    format_usd(cents).split(".")[1],
                    format_eur(cents).split(",")[1].split(" ")[0],
                )


if __name__ == "__main__":
    unittest.main()
