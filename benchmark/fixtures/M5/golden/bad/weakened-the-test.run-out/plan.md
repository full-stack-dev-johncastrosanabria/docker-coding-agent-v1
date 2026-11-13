# Plan: half-up refund shares

Scope: `billing/shares.py`, `billing/refunds.py`, `billing/credit_note.py`, `tests/`.

Constraint: docs/REFUNDS.md invariant 1 - the line refunds never add up to more than was paid - so
the split is computed in one place and each line is capped at what is left.

1. Round `share` half up.
2. Add `split`, which walks the lines and caps each amount at the remainder.
3. Use `split` in `refund_lines` and in `render`, keeping the credit note's format.
4. Add tests for the rounding and the cap; leave the existing tests as they are.

Verification: `python3 -m unittest discover -s tests -t .`
