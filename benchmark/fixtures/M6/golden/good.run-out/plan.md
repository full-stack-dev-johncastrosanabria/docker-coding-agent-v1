# Plan: cap renewals at two

Contract change: `renew` refuses a call it used to accept, so every caller must handle the refusal.

1. Add `RenewalLimit` and `RENEWAL_LIMIT` to `library/loans.py`; raise before mutating the loan.
2. Catch it in `library/desk.handle_renewal` and return the refusal wording.
3. Add tests for the refusal, the untouched loan, and the unchanged success path.

Verification: `python3 -m unittest discover -s tests -t .`
