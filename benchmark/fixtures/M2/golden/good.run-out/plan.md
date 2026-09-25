# Plan: pad cents everywhere, once

The task reads as a single format fix. Reading the package shows `billing/summary.py` repeats the
same `cents // 100` / `% 100` arithmetic, so fixing only `cents_to_text` leaves the summary wrong.
Escalated to planned: two coordinated edits enforcing one rule.

1. Pad cents in `billing/money.cents_to_text`.
2. Make `billing/summary.monthly_summary` reuse `cents_to_text` instead of its own arithmetic.
3. Add tests for single-digit cents, zero, and summary/formatter agreement.

Verification: `python3 -m unittest discover -s tests -t .`
