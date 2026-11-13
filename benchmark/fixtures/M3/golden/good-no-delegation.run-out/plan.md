# Plan: due dates on holiday weekends

Scope: `core/timeutil/workdays.py`, `tests/`.

Finding (researcher): `billing.late_fees.late_fee` -> `billing.terms.due_date` ->
`core.timeutil.workdays.add_business_days` -> `next_business_day`, which skips a weekend and then at
most one holiday, so a Friday holiday returns the Saturday.

Constraint: `shipping/calendar.py` and `reporting/business_days.py` keep their own calendars on
purpose and stay as they are.

1. Make `next_business_day` skip weekends and holidays until neither applies.
2. Add a regression test for the invoice issued on 2025-04-04.

Verification: `python3 -m unittest discover -s tests -t .`
