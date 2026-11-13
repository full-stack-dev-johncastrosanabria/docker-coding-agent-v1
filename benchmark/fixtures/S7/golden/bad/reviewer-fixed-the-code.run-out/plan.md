# Plan: per-line discount

Scope: `pricing/lines.py`, `pricing/invoice.py`, `pricing/cli.py`, `tests/`.

Constraint: the discount is computed in exactly one place, `line_total`, so a row and the invoice
total can never disagree.

1. Add `discount_percent=0` to `line_total`, with the range check and the rounded-down amount.
2. Thread whole lines through `invoice_total` so an optional third element reaches `line_total`.
3. Do the same in `render`, keeping the existing row and total format.
4. Add tests for the rounding, the absent discount, the range check and cross-component agreement.

Verification: `python3 -m unittest discover -s tests -t .`
