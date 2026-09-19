"""Planning-time contract cases, ported to unittest (tasks.md T007).

One generated test per case, 71 in total:
- 46 planning cases: 29 completion-report (run_integrity x sandbox_created, D-FIN required
  checks, none-adequate, S5a report), 5 approval-grant (provenance), and 12 fixture (IDs and
  S5a/S5b variants);
- 10 planned-task success cases (CR3);
- 3 fixture trust-level cases (M7);
- 3 native ceiling cases;
- 9 source.ref cases (refs/heads/* only).

Draft 2020-12 meta-validation of the three contracts and the 29-ID pattern check run once, in
setUpClass. Uses jsonschema, a dev/test-only dependency (requirements-dev.txt).
"""

import copy
import json
import re
import unittest
from pathlib import Path

import jsonschema

CONTRACTS = Path(__file__).resolve().parents[2] / "specs" / "001-bounded-coding-agent" / "contracts"
SCHEMA_FILES = {
    "report": "completion-report.schema.json",
    "grant": "approval-grant.schema.json",
    "fixture": "fixture.schema.json",
}
SCHEMAS = {key: json.loads((CONTRACTS / name).read_text(encoding="utf-8")) for key, name in SCHEMA_FILES.items()}

H = "a" * 64


def _check(**overrides):
    return {
        "id": "c1",
        "command_or_method": "make test",
        "required": True,
        "executed_by": "launcher",
        "after_last_change": True,
        "result": "pass",
        **overrides,
    }


BASE_REPORT = {
    "schema_version": "1.2",
    "run_id": "run-20260919T010000Z-abc123",
    "backend": "claude",
    "trust_level": "trusted",
    "task_fingerprint": "sha256:" + H,
    "source": {"ref": "refs/heads/main", "commit": "a" * 40, "bundle_sha256": "b" * 64, "uncommitted_ignored": False},
    "sandbox_settings": {"mountless": True, "shared_skills": "off", "ssh_agent_forwarding": False, "network_policy_digest": "x"},
    "run_integrity": {"stream": "complete", "agent_exit": "normal", "sandbox_created": True},
    "classification": {"value": "direct", "reason": "small"},
    "agent_outcome": "succeeded",
    "final_outcome": "succeeded",
    "outcome_overrides": [],
    "primary_reason": None,
    "human_action_required": None,
    "acceptance_criteria": [{"text": "bug fixed", "status": "satisfied"}],
    "change_set": {"branch": "dca/run-x", "base_commit": "a" * 40, "files": [{"path": "src/a.py", "status": "modified"}]},
    "verification": {"type": "deterministic", "checks": [_check(), _check(id="c2", command_or_method="make lint")]},
    "approvals": [],
    "limits": {"configured": {}, "used": {}, "limit_reached": None},
    "cost_enforced": False,
    "risks": [],
    "blockers": [],
    "versions": {"docker_agent": "v1.136.0"},
}


def _blocked(d):
    d.update(final_outcome="blocked", primary_reason="r", human_action_required="h")


def _s5a(d):
    _blocked(d)
    d.update(agent_outcome="missing", sandbox_settings=None, classification=None, verification=None,
             trust_level="untrusted", acceptance_criteria=[])
    d["run_integrity"] = {"stream": "none", "agent_exit": "not-started", "sandbox_created": False}
    d["source"]["bundle_sha256"] = None
    d["change_set"].update(files=[], branch=None)


def _none_adequate(d):
    _blocked(d)
    d["verification"] = {"type": "none-adequate", "checks": []}


def _planned(d):
    d.update(classification={"value": "planned", "reason": "multi-component"}, plan_ref="plan.md",
             review={"performed": True, "fingerprint_before": "f", "fingerprint_after": "f", "identical": True,
                     "evidence_origin": "vm", "findings": []})


NATIVE_CEILING = {"event": "budget_exceeded", "config_path": "budget.max_tokens", "budget": "run",
                  "limit": "max_tokens", "used": "8000123", "max": "8000000"}


def _all(*steps):
    def apply(d):
        for step in steps:
            step(d)
    return apply


REPORT_CASES = [
    ("succeeded, all required pass", lambda d: None, True),
    ("succeeded, 2nd required fails", lambda d: d["verification"]["checks"][1].update(result="fail"), False),
    ("succeeded, required partial", lambda d: d["verification"]["checks"][0].update(result="partial"), False),
    ("succeeded, required unresolved", lambda d: d["verification"]["checks"][0].update(result="unresolved"), False),
    ("succeeded, stale required pass", lambda d: d["verification"]["checks"][0].update(after_last_change=False), False),
    ("succeeded, no required checks", lambda d: [c.update(required=False) for c in d["verification"]["checks"]], False),
    ("succeeded, criterion unknown", lambda d: d["acceptance_criteria"][0].update(status="unknown"), False),
    ("succeeded at host limit (FR-023a)",
     _all(lambda d: d["limits"].update(limit_reached="steps"),
          lambda d: d["run_integrity"].update(stream="host-terminated", agent_exit="host-limit")), True),
    ("succeeded, malformed stream", lambda d: d["run_integrity"].update(stream="malformed"), False),
    ("succeeded, truncated stream", lambda d: d["run_integrity"].update(stream="truncated"), False),
    ("succeeded, abnormal exit", lambda d: d["run_integrity"].update(agent_exit="abnormal"), False),
    ("run_integrity without sandbox_created", lambda d: d["run_integrity"].pop("sandbox_created"), False),
    ("S5a pre-provisioning blocked report", _s5a, True),
    ("sandbox_created=false but claims succeeded", _all(_s5a, lambda d: d.update(final_outcome="succeeded")), False),
    ("sandbox_created=false with changed files",
     _all(_s5a, lambda d: d["change_set"].update(files=[{"path": "x", "status": "added"}])), False),
    ("sandbox_created=false with bundle hash", _all(_s5a, lambda d: d["source"].update(bundle_sha256="b" * 64)), False),
    ("sandbox_created=false with sandbox_settings",
     _all(_s5a, lambda d: d.update(sandbox_settings=copy.deepcopy(BASE_REPORT["sandbox_settings"]))), False),
    ("sandbox_created=false with verification",
     _all(_s5a, lambda d: d.update(verification={"type": "deterministic", "checks": []})), False),
    ("sandbox_created=false with classification",
     _all(_s5a, lambda d: d.update(classification={"value": "direct", "reason": "x"})), False),
    ("sandbox_created=false with stream complete", _all(_s5a, lambda d: d["run_integrity"].update(stream="complete")), False),
    ("sandbox_created=true with null bundle", lambda d: d["source"].update(bundle_sha256=None), False),
    ("sandbox_created=true with null sandbox_settings", _all(_blocked, lambda d: d.update(sandbox_settings=None)), False),
    ("sandbox_created=true with stream none", _all(_blocked, lambda d: d["run_integrity"].update(stream="none")), False),
    ("sandbox created, agent report missing -> blocked with null classification",
     _all(_blocked, lambda d: d.update(agent_outcome="missing", classification=None, verification=None),
          lambda d: d["run_integrity"].update(agent_exit="abnormal")), True),
    ("null verification but succeeded", lambda d: d.update(verification=None), False),
    ("none-adequate with files", _none_adequate, False),
    ("none-adequate, empty change set", _all(_none_adequate, lambda d: d["change_set"].update(files=[])), True),
    ("alternative without limitation",
     lambda d: d["verification"].update(type="alternative", alternative_definition="repro"), False),
    ("blocked without human action", lambda d: d.update(final_outcome="blocked", primary_reason="x"), False),
]

PLANNED_CASES = [
    ("planned + plan + review performed + identical -> succeeded eligible", _planned, True),
    ("planned succeeded, plan_ref null", _all(_planned, lambda d: d.update(plan_ref=None)), False),
    ("planned succeeded, plan_ref missing", _all(_planned, lambda d: d.pop("plan_ref")), False),
    ("planned succeeded, review.performed false", _all(_planned, lambda d: d["review"].update(performed=False)), False),
    ("planned succeeded, review null", _all(_planned, lambda d: d.update(review=None)), False),
    ("planned succeeded, review.identical false",
     _all(_planned, lambda d: d["review"].update(identical=False, fingerprint_after="g"),
          lambda d: d.update(safety_events=["reviewer-fingerprint-mismatch"])), False),
    ("planned blocked, identical false + safety event",
     _all(_planned, _blocked, lambda d: d["review"].update(identical=False, fingerprint_after="g"),
          lambda d: d.update(safety_events=["reviewer-fingerprint-mismatch"])), True),
    ("planned blocked, identical false, no safety event",
     _all(_planned, _blocked, lambda d: d["review"].update(identical=False, fingerprint_after="g")), False),
    ("direct succeeded without plan or review", lambda d: d.update(plan_ref=None, review=None), True),
    ("direct succeeded with review.identical false",
     lambda d: d.update(review={"performed": True, "identical": False}, safety_events=["reviewer-fingerprint-mismatch"]), False),
]

NATIVE_CEILING_CASES = [
    ("blocked at native ceiling with detail",
     _all(_blocked, lambda d: d["limits"].update(limit_reached="native_ceiling", native_ceiling=dict(NATIVE_CEILING))), True),
    ("native_ceiling without detail", _all(_blocked, lambda d: d["limits"].update(limit_reached="native_ceiling")), False),
    ("native detail with limit_reached steps",
     _all(_blocked, lambda d: d["limits"].update(limit_reached="steps", native_ceiling=dict(NATIVE_CEILING))), False),
]

SOURCE_REF_CASES = [
    ("source.ref refs/heads/main", "refs/heads/main", True),
    ("source.ref refs/heads/feature/x-1", "refs/heads/feature/x-1", True),
    ("source.ref refs/heads/dca/run-1", "refs/heads/dca/run-1", True),
    ("source.ref bare branch name", "main", False),
    ("source.ref HEAD", "HEAD", False),
    ("source.ref tag", "refs/tags/v1.0", False),
    ("source.ref remote-tracking ref", "refs/remotes/origin/main", False),
    ("source.ref raw commit SHA", "a" * 40, False),
    ("source.ref revision expression", "refs/heads/main~1", False),
]

PROVENANCE = {
    "origin_run_id": "run-20260919T010000Z-abc123",
    "origin_report_digest": "sha256:" + H,
    "task_fingerprint": "sha256:" + H,
    "source_commit": "a" * 40,
    "origin_backend": "claude",
    "origin_trust_level": "trusted",
}


def _grant(action_class, provenance=PROVENANCE):
    grant = {"request_id": "apr-run-20260919T010000Z-abc123-1", "action_class": action_class,
             "scope": {"exact": "npm add left-pad"}}
    if provenance is not None:
        grant["provenance"] = provenance
    return {"run_id": "run-20260919T020000Z-def456", "granted_by": "developer-cli", "grants": [grant]}


GRANT_CASES = [
    ("grant class 15 with provenance", _grant(15), True),
    ("grant without provenance", _grant(15, None), False),
    ("grant missing task_fingerprint", _grant(15, {k: v for k, v in PROVENANCE.items() if k != "task_fingerprint"}), False),
    ("grant class 30 (DENY)", _grant(30), False),
    ("grant class 21 (ASK)", _grant(21), True),
]

S5A = {"id": "S5a", "category": "safety-boundary", "trust_level": "untrusted", "gate_condition": "untrusted-ineligible",
       "task": "t", "acceptance_criteria": ["refuse"], "verification": {"type": "deterministic"},
       "expected_disposition": "blocked", "oracle": "oracle.sh", "allowed_change_scope": [],
       "prohibited_checks": ["no-sandbox-or-model-execution", "no-canary-leak", "no-credential-read", "no-egress-unapproved"]}
S5B = {"id": "S5b", "category": "safety-boundary", "trust_level": "untrusted", "gate_condition": "untrusted-eligible",
       "task": "t", "acceptance_criteria": ["a"], "verification": {"type": "deterministic"},
       "expected_disposition": "blocked", "oracle": "o", "allowed_change_scope": []}
K1 = {"id": "K1", "category": "small", "trust_level": "both", "task": "t", "acceptance_criteria": ["a"],
      "verification": {"type": "deterministic"}, "expected_disposition": "succeeded", "oracle": "o",
      "allowed_change_scope": ["src/**"]}

FIXTURE_CASES = [
    ("S5a fixture valid", S5A, True),
    ("S5b fixture valid", S5B, True),
    ("K1 fixture valid", K1, True),
    ("id S5 (bare) rejected", {**S5A, "id": "S5"}, False),
    ("id S9 rejected", {**K1, "id": "S9"}, False),
    ("id K9 rejected", {**K1, "id": "K9"}, False),
    ("S5a with gate_condition always", {**S5A, "gate_condition": "always"}, False),
    ("S5b as untrusted-ineligible",
     {**S5B, "gate_condition": "untrusted-ineligible", "prohibited_checks": S5A["prohibited_checks"]}, False),
    ("K1 with gate_condition untrusted-eligible", {**K1, "gate_condition": "untrusted-eligible", "trust_level": "untrusted"}, False),
    ("S5a expecting succeeded", {**S5A, "expected_disposition": "succeeded"}, False),
    ("S5a missing no-egress", {**S5A, "prohibited_checks": S5A["prohibited_checks"][:3]}, False),
    ("none-adequate expecting succeeded", {**K1, "verification": {"type": "none-adequate"}}, False),
]

FIXTURE_TRUST_CASES = [
    ("K1 with trust_level trusted rejected (M7)", {**K1, "trust_level": "trusted"}, False),
    ("M1 with trust_level untrusted rejected (M7)", {**K1, "id": "M1", "category": "medium", "trust_level": "untrusted"}, False),
    ("S5a with trust_level both rejected", {**S5A, "trust_level": "both"}, False),
]


def _report(mutate):
    def build():
        doc = copy.deepcopy(BASE_REPORT)
        mutate(doc)
        return doc
    return build


def _source_ref(ref):
    def build():
        doc = copy.deepcopy(BASE_REPORT)
        doc["source"]["ref"] = ref
        return doc
    return build


def _const(doc):
    return lambda: copy.deepcopy(doc)


CASES = (
    [("planning", "report", name, _report(m), exp) for name, m, exp in REPORT_CASES]
    + [("planning", "grant", name, _const(d), exp) for name, d, exp in GRANT_CASES]
    + [("planning", "fixture", name, _const(d), exp) for name, d, exp in FIXTURE_CASES]
    + [("planned", "report", name, _report(m), exp) for name, m, exp in PLANNED_CASES]
    + [("trust", "fixture", name, _const(d), exp) for name, d, exp in FIXTURE_TRUST_CASES]
    + [("native", "report", name, _report(m), exp) for name, m, exp in NATIVE_CEILING_CASES]
    + [("source_ref", "report", name, _source_ref(ref), exp) for name, ref, exp in SOURCE_REF_CASES]
)

PHYSICAL_FIXTURE_IDS = (
    [f"K{i}" for i in range(1, 9)] + [f"M{i}" for i in range(1, 7)] + [f"F{i}" for i in range(1, 7)]
    + ["S1", "S2", "S3", "S4", "S5a", "S5b", "S6", "S7", "S8"]
)


class ContractSchemaCases(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        for key, schema in SCHEMAS.items():
            jsonschema.Draft202012Validator.check_schema(schema)  # raises if a contract is not valid Draft 2020-12
        cls.validators = {key: jsonschema.Draft202012Validator(schema) for key, schema in SCHEMAS.items()}
        pattern = SCHEMAS["fixture"]["properties"]["id"]["pattern"]
        accepted = [i for i in PHYSICAL_FIXTURE_IDS if re.match(pattern, i)]
        if len(PHYSICAL_FIXTURE_IDS) != 29 or len(accepted) != 29:
            raise AssertionError(f"expected 29 physical fixture IDs, pattern accepts {len(accepted)}")


def _make_test(schema_key, build, expected, name):
    def test(self):
        doc = build()
        valid = self.validators[schema_key].is_valid(doc)
        self.assertEqual(valid, expected, f"{name}: expected {'valid' if expected else 'invalid'}")
    test.__doc__ = name
    return test


for _index, (_group, _schema, _name, _build, _expected) in enumerate(CASES, 1):
    _slug = re.sub(r"[^0-9a-z]+", "_", _name.lower()).strip("_")
    setattr(ContractSchemaCases, f"test_{_index:02d}_{_group}_{_slug}", _make_test(_schema, _build, _expected, _name))


if __name__ == "__main__":
    unittest.main()
