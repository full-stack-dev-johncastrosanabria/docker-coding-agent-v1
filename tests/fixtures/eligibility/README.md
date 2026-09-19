# Synthetic eligibility fixtures

These documents are **synthetic test inputs** for `tests/contract/test_gate_contracts.py`
(tasks.md T005) and for later launcher and benchmark unit tests. They are **not** gate
evidence and don't claim that any gate has run or passed. Their timestamps
(`1970-01-01T00:00:00Z`), versions (`0.0.0-synthetic`) and fingerprint (all zeros) are
deliberately unrealistic.

Every fixture is bound to `versions.synthetic.yaml`, a synthetic stand-in for
`runtime/versions.yaml` with the same keys, written in the canonical JSON-compatible YAML form
(tasks.md, Global constraints). Its gate-owned pins use the reserved `.invalid` domain,
`0.0.0-synthetic` versions and an all-zero SHA-256. Each fixture's `pinned_versions` and
`runtime_versions_digest` are derived from it with `gates/eligibility_rules.py`
(`pinned_versions()`, `runtime_versions_digest()`), so the fixtures pass the binding check against
the synthetic file and are always **stale** against the real `runtime/versions.yaml`.

Real eligibility is computed only by the gate review (T024) into `gates/eligibility.json`,
from evidence that the gates actually recorded.

| Fixture | Expected |
|---|---|
| `all-eligible.json` | valid: both backends trusted- and untrusted-eligible |
| `trusted-only.json` | valid: both trusted-eligible; G9 failed on both, so neither is untrusted-eligible |
| `claude-unavailable.json` | valid: Claude unavailable (G1a FAIL); Codex still trusted-eligible |
| `codex-unavailable.json` | valid: Codex unavailable (G3 FAIL); Claude still trusted-eligible |
| `none-eligible.json` | valid: a common gate (G4) failed, so neither backend is eligible |
| `invalid-untrusted-without-g9.json` | invalid: Claude claims untrusted eligibility while its G9 failed |
| `invalid-trusted-with-partial-g11.json` | invalid: Codex claims trusted eligibility while its G11 is only PARTIAL |
