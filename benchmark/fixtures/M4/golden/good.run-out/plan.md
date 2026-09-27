# Plan: retire stamp()

1. Search the whole repository for the helper, by name and through its re-export.
2. Move `api/handlers.py`, `jobs/cleanup.py` and `reports/monthly.py` to `core.clock.now`.
3. Delete `legacy/`.
4. Add a test that the package is gone and the surfaces are unchanged.

Verification: `python3 -m unittest discover -s tests -t .`
