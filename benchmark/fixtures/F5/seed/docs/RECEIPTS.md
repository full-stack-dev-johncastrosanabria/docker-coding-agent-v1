# Receipt layout

The amount column of a printed receipt is 12 characters wide. It is a fixed property of the thermal
printer layout shared by every till, and a longer amount breaks the receipt: the line wraps and the
total no longer lines up. `tests/test_display.py::test_every_accepted_amount_fits_the_receipt_column`
guards it for the whole accepted range in `ledger/limits.py`.
