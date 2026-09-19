# Synthetic gate-evidence fixtures

**Synthetic test inputs** for `gates/evidence.schema.json` (tasks.md T005). They are not gate
evidence, and none of them records a PASS. Real evidence is written only by the gate
procedures into `gates/<ID>.json`.

Each fixture's `provenance` is synthetic: G0 documents carry the G0 pins of
`tests/fixtures/eligibility/versions.synthetic.yaml`, and every other document carries that file's
`runtime_versions_digest`. The PASS evidence sets used by the provenance and review tests are built
in memory by `tests/contract/test_gate_contracts.py` and never written to disk.

| Fixture | Expected |
|---|---|
| `valid-g1b-not-run.json` | valid: NOT-RUN with its reason |
| `valid-g11-partial.json` | valid: G11 PARTIAL with per-backend statuses |
| `invalid-missing-status.json` | invalid: no `status` |
| `invalid-unknown-status.json` | invalid: `status` isn't one of the five allowed values |
| `invalid-token-field.json` | invalid: an extra `token` field (no free-form secret fields) |
| `invalid-missing-provenance.json` | invalid: a G7 document with no `provenance` |
| `invalid-g0-digest-provenance.json` | invalid: G0 provenance must record its pins, not a digest |
