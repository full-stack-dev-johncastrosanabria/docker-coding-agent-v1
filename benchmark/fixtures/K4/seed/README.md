# splitter

Small helpers for sharing a bill.

## Splitting amounts

`split_amount(total_cents, parts)` splits an amount, in integer cents, into `parts` amounts that
add up to it exactly:

    >>> split_amount(90, 3)
    [30, 30, 30]

`parts` must be at least 1 and the total must not be negative; anything else raises `ValueError`.

## Development

Run the tests with `python3 -m unittest discover -s tests -t .`.
