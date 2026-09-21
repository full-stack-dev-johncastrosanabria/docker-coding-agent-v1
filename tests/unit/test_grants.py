"""Unit tests for host-authoritative approval grants (tasks.md T035, src/dca/grants.py).

A grant is the only way an ASK becomes an ALLOW, so this module is the single trusted approval
channel in V1. Everything here defends one property: a grant can only be MANUFACTURED by the
launcher from an authoritative prior report, and never READ from a file someone hands us.

That is why there is no "load a grant file" function to test. Repository content and in-VM state
can produce none of the provenance fields, and the in-VM copy is never read back - it only feeds
the cooperative gate, so forging it grants nothing that matters.

Every rejection exits 3 (stale or undiscoverable approval), because the developer's remedy is
always the same: re-run and approve again.
"""

import hashlib
import importlib.util
import json
import shutil
import tempfile
import unittest
from pathlib import Path

import jsonschema

ROOT = Path(__file__).resolve().parents[2]
SCHEMA = ROOT / "specs" / "001-bounded-coding-agent" / "contracts" / "approval-grant.schema.json"


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


grants = _load("dca_grants", ROOT / "src" / "dca" / "grants.py")

ORIGIN_RUN = "run-2026-09-21T00-00-00Z-abc123"
NEW_RUN = "run-2026-09-21T01-00-00Z-def456"
REQUEST = f"apr-{ORIGIN_RUN}-1"
FINGERPRINT = "sha256:" + "a" * 64
COMMIT = "b" * 40


class GrantHarness(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.repo = self.tmp / "repo"
        self.repo.mkdir()
        self.runs = self.tmp / ".dca-runs"
        self.schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
        self.write_report()

    def report_document(self, **overrides):
        document = {
            "run_id": ORIGIN_RUN,
            "backend": "claude",
            "trust_level": "trusted",
            "task_fingerprint": FINGERPRINT,
            "source": {"commit": COMMIT, "ref": "refs/heads/main"},
            "approvals": [{
                "id": REQUEST,
                "run_id": ORIGIN_RUN,
                "action_class": 21,
                "normalized_target": "https://api.example.invalid/v1",
                "reason": "External API call",
                "risk": "medium",
                "trust_level": "trusted",
                "status": "unanswered",
            }],
        }
        document.update(overrides)
        return document

    def write_report(self, document=None, run_id=ORIGIN_RUN, path=None):
        document = self.report_document() if document is None else document
        target = path or (self.runs / run_id / "report.json")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(document, sort_keys=True), encoding="utf-8")
        return target

    def new_run(self, **overrides):
        run = {"run_id": NEW_RUN, "backend": "claude", "trust_level": "trusted",
               "task_fingerprint": FINGERPRINT, "source_commit": COMMIT}
        run.update(overrides)
        return run

    def create(self, request_ids=(REQUEST,), approval_report=None, **run_overrides):
        return grants.create_grants(request_ids=list(request_ids),
                                    new_run=self.new_run(**run_overrides),
                                    repo_root=self.repo,
                                    approval_report=approval_report)

    def assertRefuses(self, **kwargs):
        with self.assertRaises(grants.GrantError) as caught:
            self.create(**kwargs)
        self.assertEqual(caught.exception.exit_code, 3)
        return caught.exception


class Positive(GrantHarness):
    def test_01_the_default_report_location_is_discovered(self):
        """<repo>/../.dca-runs/<origin-run-id>/report.json, with the run id read from the request."""
        document = self.create()
        self.assertEqual(document["run_id"], NEW_RUN)
        self.assertEqual(document["granted_by"], "developer-cli")
        self.assertEqual(len(document["grants"]), 1)

    def test_02_an_explicit_approval_report_path_is_used(self):
        custom = self.tmp / "elsewhere" / "report.json"
        self.write_report(path=custom)
        shutil.rmtree(self.runs)
        document = self.create(approval_report=custom)
        self.assertEqual(len(document["grants"]), 1)

    def test_03_the_result_validates_against_the_contract_schema(self):
        document = self.create()
        errors = [e.message for e in
                  jsonschema.Draft202012Validator(self.schema).iter_errors(document)]
        self.assertEqual(errors, [])

    def test_04_the_provenance_binds_the_grant_to_the_origin(self):
        grant = self.create()["grants"][0]
        provenance = grant["provenance"]
        self.assertEqual(provenance["origin_run_id"], ORIGIN_RUN)
        self.assertEqual(provenance["task_fingerprint"], FINGERPRINT)
        self.assertEqual(provenance["source_commit"], COMMIT)
        self.assertEqual(provenance["origin_backend"], "claude")
        self.assertEqual(provenance["origin_trust_level"], "trusted")
        self.assertRegex(provenance["origin_report_digest"], r"\Asha256:[0-9a-f]{64}\Z")

    def test_05_the_digest_is_of_the_host_copy_of_the_report(self):
        path = self.runs / ORIGIN_RUN / "report.json"
        expected = "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()
        self.assertEqual(self.create()["grants"][0]["provenance"]["origin_report_digest"],
                         expected)

    def test_06_the_scope_carries_the_requests_normalized_target(self):
        grant = self.create()["grants"][0]
        self.assertEqual(grant["scope"], {"exact": "https://api.example.invalid/v1"})
        self.assertEqual(grant["action_class"], 21)

    def test_07_a_requested_status_is_also_grantable(self):
        document = self.report_document()
        document["approvals"][0]["status"] = "requested"
        self.write_report(document)
        self.assertEqual(len(self.create()["grants"]), 1)

    def test_08_an_equivalence_class_request_produces_an_equivalence_scope(self):
        document = self.report_document()
        document["approvals"][0].pop("normalized_target")
        document["approvals"][0]["equivalence_class"] = {
            "action_type": "external-api", "targets": ["https://api.example.invalid/v1"]}
        self.write_report(document)
        grant = self.create()["grants"][0]
        self.assertEqual(grant["scope"]["equivalence_class"]["action_type"], "external-api")


class Negative(GrantHarness):
    def test_09_a_missing_report_refuses(self):
        shutil.rmtree(self.runs)
        self.assertRefuses()

    def test_10_an_explicit_report_path_that_does_not_exist_refuses(self):
        self.assertRefuses(approval_report=self.tmp / "nope" / "report.json")

    def test_11_a_run_id_mismatch_refuses(self):
        self.write_report(self.report_document(run_id="run-2026-01-01T00-00-00Z-999999"))
        self.assertRefuses()

    def test_12_a_digest_mismatch_refuses(self):
        """A caller-supplied digest that disagrees with the located report is stale."""
        with self.assertRaises(grants.GrantError) as caught:
            grants.create_grants(request_ids=[REQUEST], new_run=self.new_run(),
                                 repo_root=self.repo,
                                 expected_report_digest="sha256:" + "c" * 64)
        self.assertEqual(caught.exception.exit_code, 3)

    def test_13_an_absent_request_refuses(self):
        self.assertRefuses(request_ids=[f"apr-{ORIGIN_RUN}-99"])

    def test_14_a_status_that_is_not_requested_or_unanswered_refuses(self):
        for status in ("granted", "denied", "expired", ""):
            with self.subTest(status=status):
                document = self.report_document()
                document["approvals"][0]["status"] = status
                self.write_report(document)
                self.assertRefuses()

    def test_15_a_deny_class_is_never_grantable(self):
        for action_class in (2, 5, 16, 19, 22, 23, 25, 26, 27, 29, 30):
            with self.subTest(action_class=action_class):
                document = self.report_document()
                document["approvals"][0]["action_class"] = action_class
                self.write_report(document)
                self.assertRefuses()

    def test_16_a_class_mismatch_refuses(self):
        """An action class outside 1-31 cannot be carried into a grant."""
        document = self.report_document()
        document["approvals"][0]["action_class"] = 999
        self.write_report(document)
        self.assertRefuses()

    def test_17_a_request_with_no_target_or_equivalence_class_refuses(self):
        document = self.report_document()
        document["approvals"][0].pop("normalized_target")
        self.write_report(document)
        self.assertRefuses()

    def test_18_a_source_commit_mismatch_refuses(self):
        self.assertRefuses(source_commit="d" * 40)

    def test_19_a_task_fingerprint_mismatch_refuses(self):
        self.assertRefuses(task_fingerprint="sha256:" + "e" * 64)

    def test_20_a_trust_level_mismatch_refuses(self):
        self.assertRefuses(trust_level="untrusted")

    def test_21_a_backend_mismatch_refuses(self):
        self.assertRefuses(backend="codex")

    def test_22_a_malformed_request_id_refuses(self):
        for bad in ("nonsense", "apr-", "apr-noorigin-1", ""):
            with self.subTest(request_id=bad):
                self.assertRefuses(request_ids=[bad])

    def test_23_an_unreadable_or_invalid_report_refuses(self):
        (self.runs / ORIGIN_RUN / "report.json").write_text("{not json", encoding="utf-8")
        self.assertRefuses()


class Guarantees(GrantHarness):
    def test_24_no_code_path_reads_a_grant_file_as_input(self):
        """V1 has no manually authored grant-file input, so no such entry point may exist."""
        public = [name for name in dir(grants) if not name.startswith("_")]
        for name in public:
            with self.subTest(symbol=name):
                lowered = name.lower()
                self.assertFalse(
                    ("load" in lowered or "read" in lowered or "parse" in lowered)
                    and "grant" in lowered,
                    f"{name} looks like a grant-file reader")

    def test_25_the_module_never_opens_a_path_named_like_a_grant_file(self):
        source = (ROOT / "src" / "dca" / "grants.py").read_text(encoding="utf-8")
        for forbidden in ("grants.json", "grant.json", "grants.yaml"):
            with self.subTest(name=forbidden):
                self.assertNotIn(forbidden, source,
                                 "the in-VM grant copy is never read back")

    def test_26_every_refusal_uses_exit_code_three(self):
        self.assertEqual(grants.GrantError.exit_code, 3)

    def test_27_provenance_cannot_be_supplied_by_the_caller(self):
        """Repository content and in-VM state must not be able to produce any provenance field."""
        document = self.create()
        grant = document["grants"][0]
        self.assertEqual(sorted(grant["provenance"]),
                         ["origin_backend", "origin_report_digest", "origin_run_id",
                          "origin_trust_level", "source_commit", "task_fingerprint"])

    def test_28_multiple_requests_produce_multiple_grants(self):
        document = self.report_document()
        second = dict(document["approvals"][0], id=f"apr-{ORIGIN_RUN}-2",
                      normalized_target="https://api.example.invalid/v2")
        document["approvals"].append(second)
        self.write_report(document)
        result = self.create(request_ids=[REQUEST, f"apr-{ORIGIN_RUN}-2"])
        self.assertEqual(len(result["grants"]), 2)


if __name__ == "__main__":
    unittest.main()
