"""The ledger's accepted range. A Finance decision, shared with validation and the receipt printer."""

#: The largest amount the ledger accepts, in cents: 999,999,999.99.
MAX_CENTS = 99_999_999_999
#: The smallest: zero. Refunds are separate documents, never negative receipt lines.
MIN_CENTS = 0
