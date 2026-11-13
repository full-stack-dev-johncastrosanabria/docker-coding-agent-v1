# Refunds

A full refund is split across the order's lines in proportion to each line's price.

Invariants, in order of precedence:

1. **A refund never exceeds what the customer paid.** The line refunds of an order never add up to
   more than the amount paid. This is a legal requirement, not a preference.
2. Every line refund is a whole number of cents.
3. `billing.refunds.refund_lines` and `billing.credit_note.render` always show the same amounts:
   the credit note is what the customer is actually refunded.

Whenever a rounding rule would break invariant 1, a line is refunded only what is left: going down
the order, each line gets its rounded share, but never more than the amount paid minus what the
lines before it were already refunded.
