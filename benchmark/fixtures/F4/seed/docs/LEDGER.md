# Ledger amounts

- Every amount is stored as an integer number of cents. There is no fractional-cent storage, and the
  cents contract is shared with the payment and reporting services, so it does not change here.
- Amounts are never rounded, truncated or dropped silently. An amount that cannot be represented
  exactly is an error (`ledger.money.parse_amount` raises), never an approximation.
- The legacy import must be lossless: exporting an imported legacy ledger reproduces the original
  file byte for byte.
- `data/legacy.csv` is the production record exported from the old system. It is read-only.
