# rules/vat.json

    {"schema": 3,
     "jurisdictions": {"<ISO country>": {"<rate kind>": [{"rate": "<decimal>", "from": "<YYYY-MM-DD>"}]}}}

Each rate kind (`standard`, `reduced`, `zero`) is a list of effective-dated entries. The engine uses,
for an invoice, the entry with the latest `from` date that is not after the invoice date. Entries
are kept in ascending `from` order and are never edited once certified; a change in rate is a new
entry.
