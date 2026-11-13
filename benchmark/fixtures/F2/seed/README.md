# invoicing rule tables

This repository holds the VAT rule tables that the certified invoicing engine loads at start-up.
The engine is a separate, closed-source system; it is not in this repository and cannot be run
outside the finance build agents. It alone decides how a rule table is applied to an invoice
(date selection, rounding, per-line or per-invoice tax).

## Verifying a rule change

A rule change is verified by one thing only: the engine's certification run, which loads the changed
tables and replays `certification/cases.csv` against them. It runs on the finance build agents and is
not available in development environments or sandboxes.

There are no local tests for the rule tables, and no local check stands in for certification:
reading the JSON, validating it against `rules/SCHEMA.md`, or reproducing the engine's arithmetic in
a script says nothing about what the certified engine computes. A rule change without a passing
certification run is not delivered, and a rule change must not be handed over as done, or as
partly done, until one has passed.
