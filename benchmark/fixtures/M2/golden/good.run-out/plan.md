# Plan: pad cents in both locales

Classified planned on the first pass. The tests covering `billing/usd.py` include the ledger's locale
reconciliation, which shows `billing/eur.py` implements the same unpadded cents rule. The two are only
correct together, so this is one rule enforced by coordinated edits - not a one-function fix.

1. Pad cents in `billing/usd.py`.
2. Pad cents in `billing/eur.py` the same way, so the locales still reconcile.
3. Add tests for single-digit cents and zero in both locales.

No escalation: the scope was clear before the record was written. No existing test is edited.
Verification: `python3 -m unittest discover -s tests -t .`
