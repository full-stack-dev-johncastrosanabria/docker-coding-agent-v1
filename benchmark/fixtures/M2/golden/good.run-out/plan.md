# Plan: pad cents in both locales

Escalated from direct. The bounded read supported a one-function fix in `billing/usd.py`. Padding it
made the ledger's locale reconciliation in `tests/test_books.py` - which already passed - fail,
because `billing/eur.py` implements the same unpadded cents rule. The two are only correct together.

1. Pad cents in `billing/usd.py`.
2. Pad cents in `billing/eur.py` the same way, so the locales still reconcile.
3. Add tests for single-digit cents and zero in both locales.

No existing test is edited. Verification: `python3 -m unittest discover -s tests -t .`
