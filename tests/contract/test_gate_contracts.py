"""Contract tests for gate evidence and eligibility (tasks.md T005).

Run in the development environment (requirements-dev.txt): these tests use
jsonschema, which is a dev/test-only dependency and never imported by the runtime.
"""

import contextlib
import copy
import importlib.util
import io
import json
import tempfile
import unittest
from pathlib import Path

import jsonschema

ROOT = Path(__file__).resolve().parents[2]
GATES = ROOT / "gates"
CONTRACTS = ROOT / "specs" / "001-bounded-coding-agent" / "contracts"
RUNTIME_VERSIONS = ROOT / "runtime" / "versions.yaml"
ELIGIBILITY_FIXTURES = ROOT / "tests" / "fixtures" / "eligibility"
SYNTHETIC_VERSIONS = ELIGIBILITY_FIXTURES / "versions.synthetic.yaml"
EVIDENCE_FIXTURES = ROOT / "tests" / "fixtures" / "evidence"

# Stands in for the digest G4 records; never a real fingerprint of any global policy state.
SYNTHETIC_FINGERPRINT = "sha256:" + "5a" * 32

# Stands in for the set G4 proves; never a real allowance for any backend.
SYNTHETIC_ALLOWSET = {
    "claude": {"trusted": ["synthetic.invalid"], "untrusted": ["synthetic.invalid"]},
    "codex": {"trusted": ["synthetic.invalid"], "untrusted": ["synthetic.invalid"]},
}

VALID_ELIGIBILITY = (
    "all-eligible",
    "trusted-only",
    "claude-unavailable",
    "codex-unavailable",
    "none-eligible",
)
INVALID_ELIGIBILITY = (
    "invalid-untrusted-without-g9",
    "invalid-trusted-with-partial-g11",
)
VALID_EVIDENCE = ("valid-g1b-not-run", "valid-g11-partial")
INVALID_EVIDENCE = (
    "invalid-missing-status",
    "invalid-unknown-status",
    "invalid-token-field",
    "invalid-missing-provenance",
    "invalid-g0-digest-provenance",
)


def _load_json(path):
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def _shape(obj):
    """Nested key structure of a JSON object, ignoring values."""
    if isinstance(obj, dict):
        return {key: _shape(value) for key, value in obj.items()}
    return None


def _patterns(node):
    """Every "pattern" keyword value in a schema."""
    if isinstance(node, dict):
        for key, value in node.items():
            if key == "pattern" and isinstance(value, str):
                yield value
            else:
                yield from _patterns(value)
    elif isinstance(node, list):
        for item in node:
            yield from _patterns(item)


def _load_rules():
    spec = importlib.util.spec_from_file_location("eligibility_rules", GATES / "eligibility_rules.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class GateContractSchemas(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.evidence_schema = _load_json(GATES / "evidence.schema.json")
        cls.eligibility_schema = _load_json(GATES / "eligibility.schema.json")
        cls.evidence = jsonschema.Draft202012Validator(cls.evidence_schema)
        cls.eligibility = jsonschema.Draft202012Validator(cls.eligibility_schema)
        cls.rules = _load_rules()
        cls.synthetic_versions = _load_json(SYNTHETIC_VERSIONS)
        cls.runtime_versions = _load_json(RUNTIME_VERSIONS)

    def fixture(self, name):
        return _load_json(ELIGIBILITY_FIXTURES / f"{name}.json")

    def evidence_set(self, doc, versions):
        """Synthetic in-memory gate evidence (never written under gates/) whose statuses support doc
        and whose provenance is taken from versions, the way each gate records it when it runs."""

        def evidence(gate, status, backends=None):
            ev = {
                "gate": gate,
                "status": status,
                "run_at": "1970-01-01T00:00:00Z",
                "versions": {},
                "provenance": self.rules.evidence_provenance(gate, versions),
                "criteria": [],
                "notes": "SYNTHETIC in-memory test evidence: not a gate result",
            }
            if status == "NOT-RUN":
                ev["not_run_reason"] = "SYNTHETIC"
            if status == "PASS" and gate in ("G4", "PRODUCTION-CONFORMANCE"):
                # T015: a G4 PASS records the global network-policy fingerprint as a typed field,
                # and T062 re-records the same one, so T024 never parses it out of prose.
                ev["network_policy_fingerprint"] = SYNTHETIC_FINGERPRINT
            if status == "PASS" and gate == "G4":
                # T015/T051: the proven allow set is typed and written only on a PASS.
                ev["proven_network_allowset"] = SYNTHETIC_ALLOWSET
            if backends is not None:
                ev["backends"] = {
                    name: {"status": s, **({"not_run_reason": "SYNTHETIC"} if s == "NOT-RUN" else {})}
                    for name, s in backends.items()
                }
            return ev

        def summary(gate, statuses):
            if all(s == "PASS" for s in statuses):
                return "PASS"
            if all(s == "NOT-RUN" for s in statuses):
                return "NOT-RUN"
            return "PARTIAL" if gate == "G11" and "PARTIAL" in statuses else "FAIL"

        backends = doc["backends"]
        evidence_by_id = {gate: evidence(gate, status) for gate, status in doc["common_gates"].items()}
        for name, backend in backends.items():
            for gate, status in backend["gate_status"].items():
                if gate not in self.rules.PER_BACKEND_GATES:
                    evidence_by_id[gate] = evidence(gate, status)
        per_backend = {
            gate: {name: b["gate_status"][gate] for name, b in backends.items()}
            for gate in self.rules.PER_BACKEND_GATES
        }
        per_backend["PRODUCTION-CONFORMANCE"] = {
            name: b["production_conformance"] for name, b in backends.items()
        }
        for gate, statuses in per_backend.items():
            evidence_by_id[gate] = evidence(gate, summary(gate, list(statuses.values())), statuses)
        evidence_by_id["INVENTORY"] = evidence("INVENTORY", "PASS")
        return evidence_by_id

    def changed(self, versions, path, value):
        versions = copy.deepcopy(versions)
        target = versions
        for key in path[:-1]:
            target = target[key]
        target[path[-1]] = value
        return versions

    def regenerated(self, doc, versions):
        """doc re-bound to versions, as a review run after a pin change would write it."""
        doc = copy.deepcopy(doc)
        doc["pinned_versions"] = self.rules.pinned_versions(versions)
        doc["runtime_versions_digest"] = self.rules.runtime_versions_digest(versions)
        return doc

    def test_schemas_are_draft_2020_12(self):
        for schema in (self.evidence_schema, self.eligibility_schema):
            self.assertEqual(schema["$schema"], "https://json-schema.org/draft/2020-12/schema")
            jsonschema.Draft202012Validator.check_schema(schema)

    def test_schema_patterns_are_portable_ecma_262(self):
        # Draft 2020-12 patterns are ECMA-262; Python-only inline flags such as (?i) are not portable.
        schemas = [self.evidence_schema, self.eligibility_schema]
        schemas += [_load_json(path) for path in sorted(CONTRACTS.glob("*.schema.json"))]
        for schema in schemas:
            for pattern in _patterns(schema):
                with self.subTest(schema=schema.get("$id"), pattern=pattern):
                    self.assertNotRegex(pattern, r"\(\?[a-zA-Z]+[:)]")

    # --- eligibility -----------------------------------------------------------------

    def test_valid_eligibility_fixtures_are_accepted_by_schema_and_checker(self):
        for name in VALID_ELIGIBILITY:
            with self.subTest(fixture=name):
                doc = self.fixture(name)
                errors = [e.message for e in self.eligibility.iter_errors(doc)]
                self.assertEqual(errors, [])
                self.assertEqual(self.rules.check(doc), [])

    def test_invalid_eligibility_fixtures_are_rejected_by_schema_and_checker(self):
        for name in INVALID_ELIGIBILITY:
            with self.subTest(fixture=name):
                doc = self.fixture(name)
                self.assertFalse(self.eligibility.is_valid(doc))
                self.assertNotEqual(self.rules.check(doc), [])

    def test_claude_unavailable_leaves_codex_trusted_eligible(self):
        backends = self.fixture("claude-unavailable")["backends"]
        self.assertFalse(backends["claude"]["available"])
        self.assertEqual(backends["claude"]["production_conformance"], "NOT-APPLICABLE")
        self.assertTrue(backends["claude"]["unavailable_reason"])
        self.assertTrue(backends["codex"]["trusted_eligible"])

    def test_codex_unavailable_leaves_claude_trusted_eligible(self):
        backends = self.fixture("codex-unavailable")["backends"]
        self.assertFalse(backends["codex"]["available"])
        self.assertEqual(backends["codex"]["production_conformance"], "NOT-APPLICABLE")
        self.assertTrue(backends["claude"]["trusted_eligible"])

    def test_common_gate_failure_makes_both_backends_ineligible(self):
        doc = self.fixture("none-eligible")
        computed = self.rules.compute(doc)
        self.assertFalse(computed["claude"]["trusted_eligible"])
        self.assertFalse(computed["codex"]["trusted_eligible"])
        for backend in ("claude", "codex"):
            with self.subTest(backend=backend):
                over_claim = copy.deepcopy(doc)
                over_claim["backends"][backend]["trusted_eligible"] = True
                self.assertFalse(self.eligibility.is_valid(over_claim))

    def test_one_backend_never_affects_the_other(self):
        doc = self.fixture("all-eligible")
        before = self.rules.compute(doc)["claude"]
        for gate in doc["backends"]["codex"]["gate_status"]:
            doc["backends"]["codex"]["gate_status"][gate] = "FAIL"
        self.assertEqual(self.rules.compute(doc)["claude"], before)

    def test_partial_g11_never_counts_as_final(self):
        doc = self.fixture("all-eligible")
        doc["backends"]["claude"]["gate_status"]["G11"] = "PARTIAL"
        self.assertFalse(self.rules.compute(doc)["claude"]["trusted_eligible"])

    def test_checker_rejects_under_claimed_eligibility(self):
        # The schema only enforces soundness; the checker also rejects a flag below its evidence.
        doc = self.fixture("all-eligible")
        doc["backends"]["codex"]["untrusted_eligible"] = False
        self.assertTrue(self.eligibility.is_valid(doc))
        self.assertNotEqual(self.rules.check(doc), [])

    def test_available_must_match_the_availability_gate(self):
        doc = self.fixture("all-eligible")
        doc["backends"]["codex"]["gate_status"]["G3"] = "FAIL"
        self.assertFalse(self.eligibility.is_valid(doc))

    def test_unavailable_backend_requires_reason_and_not_applicable_conformance(self):
        doc = self.fixture("claude-unavailable")
        without_reason = copy.deepcopy(doc)
        del without_reason["backends"]["claude"]["unavailable_reason"]
        self.assertFalse(self.eligibility.is_valid(without_reason))
        conformance_claimed = copy.deepcopy(doc)
        conformance_claimed["backends"]["claude"]["production_conformance"] = "PASS"
        self.assertFalse(self.eligibility.is_valid(conformance_claimed))

    def test_g4_pass_requires_a_network_policy_fingerprint(self):
        doc = self.fixture("all-eligible")
        doc["network_policy_fingerprint"] = None
        self.assertFalse(self.eligibility.is_valid(doc))

    def test_synthetic_fixtures_cannot_pass_for_real_evidence(self):
        for path in sorted(ELIGIBILITY_FIXTURES.glob("*.json")):
            with self.subTest(fixture=path.name):
                doc = _load_json(path)
                self.assertEqual(doc["generated_at"], "1970-01-01T00:00:00Z")
                self.assertEqual(doc["pinned_versions"]["sbx"], "0.0.0-synthetic")
                # Bound to the synthetic versions only; always stale against the real pins.
                self.assertEqual(self.rules.check_binding(doc, self.synthetic_versions), [])
                self.assertNotEqual(self.rules.check_binding(doc, self.runtime_versions), [])

    # --- pin binding -----------------------------------------------------------------

    def test_synthetic_versions_mirror_the_runtime_versions_file(self):
        text = SYNTHETIC_VERSIONS.read_text(encoding="utf-8")
        canonical = json.dumps(self.synthetic_versions, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
        self.assertEqual(text, canonical)
        self.assertEqual(_shape(self.synthetic_versions), _shape(self.runtime_versions))

    def test_runtime_versions_digest_is_canonical(self):
        digest = self.rules.runtime_versions_digest(self.synthetic_versions)
        self.assertRegex(digest, r"^sha256:[0-9a-f]{64}$")
        # Key order and whitespace don't change the digest; only content does.
        reordered = dict(reversed(list(copy.deepcopy(self.synthetic_versions).items())))
        self.assertEqual(self.rules.runtime_versions_digest(reordered), digest)
        self.assertNotEqual(self.rules.runtime_versions_digest(self.runtime_versions), digest)

    def test_pinned_versions_are_extracted_from_the_versions_file(self):
        doc = self.fixture("all-eligible")
        self.assertEqual(doc["pinned_versions"], self.rules.pinned_versions(self.synthetic_versions))
        self.assertEqual(
            doc["runtime_versions_digest"], self.rules.runtime_versions_digest(self.synthetic_versions)
        )

    def test_any_changed_pin_makes_eligibility_stale(self):
        doc = self.fixture("all-eligible")
        changes = {
            "docker-agent artifact sha256": (("docker_agent_artifact", "sha256"), "f" * 64),
            "docker-agent artifact url": (("docker_agent_artifact", "url"), "https://synthetic.invalid/other"),
            "claude sandbox base": (("sandbox_bases", "claude", "base"), "synthetic.invalid/other-base"),
            "claude sandbox version": (("sandbox_bases", "claude", "version"), "0.0.1-synthetic"),
            "codex sandbox base": (("sandbox_bases", "codex", "base"), "synthetic.invalid/other-base"),
            "codex sandbox version": (("sandbox_bases", "codex", "version"), "0.0.1-synthetic"),
            "exact sbx": (("sbx", "exact"), "0.0.1-synthetic"),
            "exact claude code": (("claude_code", "exact"), "0.0.1-synthetic"),
            "docker agent": (("docker_agent",), "v0.0.0-synthetic"),
            "config version": (("docker_agent_config_version",), 16),
            "harness module": (("harness_module",), "example.invalid/harness@0"),
        }
        for label, (path, value) in changes.items():
            with self.subTest(change=label):
                versions = copy.deepcopy(self.synthetic_versions)
                target = versions
                for key in path[:-1]:
                    target = target[key]
                target[path[-1]] = value
                problems = self.rules.check_binding(doc, versions)
                self.assertTrue(any("runtime_versions_digest" in p for p in problems), problems)
                # The document itself is still well-formed; only the binding is stale.
                self.assertEqual(self.rules.check(doc), [])

    def test_explicit_pin_mismatch_is_detected_even_with_a_matching_digest(self):
        doc = self.fixture("all-eligible")
        for field, value in (
            ("docker_agent_artifact_sha256", "f" * 64),
            ("sbx", "0.0.1-synthetic"),
        ):
            with self.subTest(field=field):
                edited = copy.deepcopy(doc)
                edited["pinned_versions"][field] = value
                self.assertEqual(
                    self.rules.check_binding(edited, self.synthetic_versions),
                    [f"stale: pinned_versions.{field} is {value!r} but runtime/versions.yaml pins "
                     f"{doc['pinned_versions'][field]!r}"],
                )
        edited = copy.deepcopy(doc)
        edited["pinned_versions"]["sandbox_bases"]["codex"]["base"] = "synthetic.invalid/other-base"
        problems = self.rules.check_binding(edited, self.synthetic_versions)
        self.assertEqual(len(problems), 1)
        self.assertIn("pinned_versions.sandbox_bases.codex.base", problems[0])

    def test_trusted_eligible_backend_requires_non_null_pins(self):
        doc = self.fixture("all-eligible")
        pins = {
            "claude": ("docker_agent_artifact_sha256", "sbx", "claude_code", "sandbox_bases.claude.base",
                       "sandbox_bases.claude.version"),
            "codex": ("docker_agent_artifact_sha256", "sbx", "sandbox_bases.codex.base",
                      "sandbox_bases.codex.version"),
        }
        for backend, fields in pins.items():
            for field in fields:
                with self.subTest(backend=backend, field=field):
                    edited = copy.deepcopy(doc)
                    target = edited["pinned_versions"]
                    keys = field.split(".")
                    for key in keys[:-1]:
                        target = target[key]
                    target[keys[-1]] = None
                    self.assertFalse(self.eligibility.is_valid(edited))
                    self.assertIn(
                        f"{backend} is trusted-eligible but pinned_versions.{field} is null",
                        self.rules.check(edited),
                    )

    def test_null_pins_are_allowed_while_no_backend_is_trusted_eligible(self):
        doc = self.fixture("none-eligible")
        for field in ("docker_agent_artifact_sha256", "sbx", "claude_code"):
            doc["pinned_versions"][field] = None
        for backend in ("claude", "codex"):
            doc["pinned_versions"]["sandbox_bases"][backend] = {"base": None, "version": None}
        self.assertEqual([e.message for e in self.eligibility.iter_errors(doc)], [])
        self.assertEqual(self.rules.check(doc), [])

    def test_binding_fields_are_required_and_closed(self):
        doc = self.fixture("all-eligible")
        without_digest = copy.deepcopy(doc)
        del without_digest["runtime_versions_digest"]
        self.assertFalse(self.eligibility.is_valid(without_digest))
        self.assertNotEqual(self.rules.check_binding(without_digest, self.synthetic_versions), [])
        malformed_digest = copy.deepcopy(doc)
        malformed_digest["runtime_versions_digest"] = "sha256:not-a-digest"
        self.assertFalse(self.eligibility.is_valid(malformed_digest))
        without_bases = copy.deepcopy(doc)
        del without_bases["pinned_versions"]["sandbox_bases"]
        self.assertFalse(self.eligibility.is_valid(without_bases))
        extra_pin = copy.deepcopy(doc)
        extra_pin["pinned_versions"]["api_token"] = "x"
        self.assertFalse(self.eligibility.is_valid(extra_pin))

    def test_checker_cli_binds_to_a_versions_file(self):
        fixture = str(ELIGIBILITY_FIXTURES / "all-eligible.json")
        self.assertEqual(self.rules.main(["eligibility_rules.py", fixture]), 0)
        self.assertEqual(self.rules.main(["eligibility_rules.py", fixture, str(SYNTHETIC_VERSIONS)]), 0)
        with contextlib.redirect_stdout(io.StringIO()) as out:
            self.assertEqual(self.rules.main(["eligibility_rules.py", fixture, str(RUNTIME_VERSIONS)]), 1)
        self.assertIn("stale: runtime_versions_digest", out.getvalue())

    # --- gate-evidence provenance -----------------------------------------------------

    def test_synthetic_evidence_sets_are_schema_valid(self):
        for name in VALID_ELIGIBILITY:
            evidence_by_id = self.evidence_set(self.fixture(name), self.synthetic_versions)
            for gate, evidence in evidence_by_id.items():
                with self.subTest(fixture=name, gate=gate):
                    self.assertEqual([e.message for e in self.evidence.iter_errors(evidence)], [])

    def test_the_network_policy_fingerprint_is_required_of_a_g4_or_conformance_pass(self):
        # T015/T062: T024 propagates G4's fingerprint, so it is a typed field the contract
        # demands, never prose a reader has to parse back out of notes or evidence_ref.
        evidence_by_id = self.evidence_set(self.fixture("all-eligible"), self.synthetic_versions)
        for gate in ("G4", "PRODUCTION-CONFORMANCE"):
            passing = evidence_by_id[gate]
            with self.subTest(gate=gate, case="pass carries it"):
                self.assertEqual(passing["status"], "PASS")
                self.assertEqual(passing["network_policy_fingerprint"], SYNTHETIC_FINGERPRINT)
                self.assertEqual([e.message for e in self.evidence.iter_errors(passing)], [])
            with self.subTest(gate=gate, case="pass without it is invalid"):
                without = {k: v for k, v in passing.items() if k != "network_policy_fingerprint"}
                self.assertIn(
                    "'network_policy_fingerprint' is a required property",
                    [e.message for e in self.evidence.iter_errors(without)],
                )
            for bad in ("sha256:" + "5A" * 32, "5a" * 32, "sha256:beef", "", None):
                with self.subTest(gate=gate, case=f"malformed {bad!r}"):
                    malformed = dict(passing, network_policy_fingerprint=bad)
                    self.assertNotEqual([e.message for e in self.evidence.iter_errors(malformed)], [])

    def test_the_proven_allowset_is_typed_pass_only_and_g4_only(self):
        # T051 and T061 consume this field directly, so a set nothing verified must never appear,
        # and it must never have to be parsed back out of a prose sentence.
        evidence_by_id = self.evidence_set(self.fixture("all-eligible"), self.synthetic_versions)
        passing = evidence_by_id["G4"]
        self.assertEqual(passing["status"], "PASS")
        self.assertEqual(passing["proven_network_allowset"], SYNTHETIC_ALLOWSET)
        self.assertEqual([e.message for e in self.evidence.iter_errors(passing)], [])

        without = {k: v for k, v in passing.items() if k != "proven_network_allowset"}
        self.assertIn("'proven_network_allowset' is a required property",
                      [e.message for e in self.evidence.iter_errors(without)])

        for status in ("FAIL", "NOT-RUN"):
            doc = dict(passing, status=status)
            if status == "NOT-RUN":
                doc["not_run_reason"] = "SYNTHETIC"
            with self.subTest(status=status):
                self.assertNotEqual([e.message for e in self.evidence.iter_errors(doc)], [])

        with self.subTest(case="another gate may not carry one"):
            self.assertNotEqual(
                [e.message for e in self.evidence.iter_errors(dict(passing, gate="G5"))], [])

    def test_only_g4_binds_itself_to_the_committed_t014_inputs(self):
        evidence_by_id = self.evidence_set(self.fixture("all-eligible"), self.synthetic_versions)
        bound = dict(evidence_by_id["G4"], inventory_inputs={
            "inventory_evidence_sha256": SYNTHETIC_FINGERPRINT,
            "control_plane_hosts_sha256": SYNTHETIC_FINGERPRINT,
            "trusted_allowlist_draft_sha256": SYNTHETIC_FINGERPRINT,
        })
        self.assertEqual([e.message for e in self.evidence.iter_errors(bound)], [])
        self.assertNotEqual(
            [e.message for e in self.evidence.iter_errors(dict(bound, gate="G5"))], [])
        for bad in ({"inventory_evidence_sha256": SYNTHETIC_FINGERPRINT},
                    {**bound["inventory_inputs"], "extra": SYNTHETIC_FINGERPRINT},
                    {**bound["inventory_inputs"], "control_plane_hosts_sha256": "nope"}):
            with self.subTest(bad=bad):
                self.assertNotEqual(
                    [e.message for e in self.evidence.iter_errors(
                        dict(bound, inventory_inputs=bad))], [])

    def test_a_failing_gate_is_not_asked_for_a_fingerprint_it_never_proved(self):
        # Fail-closed means a FAIL records no fingerprint, not that a FAIL must invent one.
        evidence_by_id = self.evidence_set(self.fixture("all-eligible"), self.synthetic_versions)
        for status in ("FAIL", "NOT-RUN"):
            doc = dict(evidence_by_id["G4"], status=status)
            doc.pop("network_policy_fingerprint")
            doc.pop("proven_network_allowset")   # a FAIL proves no allow set either
            if status == "NOT-RUN":
                doc["not_run_reason"] = "SYNTHETIC"
            with self.subTest(status=status):
                self.assertEqual([e.message for e in self.evidence.iter_errors(doc)], [])

    def test_unchanged_evidence_and_pins_are_accepted(self):
        for name in VALID_ELIGIBILITY:
            with self.subTest(fixture=name):
                doc = self.fixture(name)
                evidence_by_id = self.evidence_set(doc, self.synthetic_versions)
                self.assertEqual(self.rules.check_evidence(evidence_by_id, self.synthetic_versions), [])
                self.assertEqual(self.rules.check_review(doc, evidence_by_id, self.synthetic_versions), [])

    def test_old_eligibility_with_a_changed_pin_is_stale(self):
        doc = self.fixture("all-eligible")
        evidence_by_id = self.evidence_set(doc, self.synthetic_versions)
        versions = self.changed(
            self.synthetic_versions, ("sandbox_bases", "codex", "base"), "synthetic.invalid/other"
        )
        self.assertNotEqual(self.rules.check_binding(doc, versions), [])
        self.assertNotEqual(self.rules.check_review(doc, evidence_by_id, versions), [])

    def test_regenerated_eligibility_from_old_evidence_is_rejected(self):
        # A review re-run after a pin change can't re-bind evidence that proved the old pins.
        old = self.fixture("all-eligible")
        old_evidence = self.evidence_set(old, self.synthetic_versions)
        versions = self.changed(self.synthetic_versions, ("docker_agent_artifact", "sha256"), "f" * 64)
        new = self.regenerated(old, versions)
        self.assertEqual(self.rules.check(new), [])
        self.assertEqual(self.rules.check_binding(new, versions), [])  # the new file is bound...
        problems = self.rules.check_review(new, old_evidence, versions)  # ...but its evidence is stale
        stale = {p.split(":")[0] for p in problems if ": stale:" in p}
        # The artifact isn't a G0 pin, so G0 stays current; G6 and every later gate must be re-run.
        self.assertEqual(stale, set(old_evidence) - {"G0"})
        # Re-running the stale gates under the new pins makes the regenerated review valid.
        self.assertEqual(self.rules.check_review(new, self.evidence_set(new, versions), versions), [])

    def test_review_rejects_statuses_without_current_evidence(self):
        doc = self.fixture("all-eligible")
        self.assertNotEqual(self.rules.check_review(doc, {}, self.synthetic_versions), [])
        evidence_by_id = self.evidence_set(doc, self.synthetic_versions)
        evidence_by_id["G9"]["backends"]["codex"]["status"] = "FAIL"
        evidence_by_id["G5"]["status"] = "FAIL"
        evidence_by_id["PRODUCTION-CONFORMANCE"]["backends"]["claude"]["status"] = "FAIL"
        problems = self.rules.check_review(doc, evidence_by_id, self.synthetic_versions)
        self.assertIn("codex.gate_status.G9 is 'PASS' but its evidence gives 'FAIL'", problems)
        self.assertIn("common_gates.G5 is 'PASS' but its evidence gives 'FAIL'", problems)
        self.assertIn("claude.production_conformance is 'PASS' but its evidence gives 'FAIL'", problems)

    def test_g0_evidence_with_a_changed_sbx_is_stale(self):
        g0 = self.evidence_set(self.fixture("all-eligible"), self.synthetic_versions)["G0"]
        versions = self.changed(self.synthetic_versions, ("sbx", "exact"), "0.0.1-synthetic")
        self.assertEqual(
            self.rules.evidence_problems(g0, versions),
            [
                "G0: stale: provenance.pins.sbx is '0.0.0-synthetic' "
                "but runtime/versions.yaml pins '0.0.1-synthetic'"
            ],
        )

    def test_g0_evidence_survives_the_pins_g6_adds_later(self):
        # G0 runs before G6 populates runtime/versions.yaml, so it is bound to its own pins, not a digest.
        pre_g6 = copy.deepcopy(self.synthetic_versions)
        pre_g6["docker_agent_artifact"] = {"sha256": None, "url": None, "verification": None}
        pre_g6["sandbox_bases"] = {b: {"base": None, "version": None} for b in ("claude", "codex")}
        g0 = {"gate": "G0", "provenance": self.rules.evidence_provenance("G0", pre_g6)}
        self.assertEqual(self.rules.evidence_problems(g0, self.synthetic_versions), [])
        g7 = {"gate": "G7", "provenance": self.rules.evidence_provenance("G7", pre_g6)}
        self.assertNotEqual(self.rules.evidence_problems(g7, self.synthetic_versions), [])

    def test_g6_evidence_with_a_changed_artifact_or_base_is_stale(self):
        g6 = self.evidence_set(self.fixture("all-eligible"), self.synthetic_versions)["G6"]
        for label, path, value in (
            ("artifact sha256", ("docker_agent_artifact", "sha256"), "f" * 64),
            ("claude base", ("sandbox_bases", "claude", "base"), "synthetic.invalid/other"),
            ("codex version", ("sandbox_bases", "codex", "version"), "0.0.1-synthetic"),
        ):
            with self.subTest(change=label):
                versions = self.changed(self.synthetic_versions, path, value)
                problems = self.rules.evidence_problems(g6, versions)
                self.assertEqual(len(problems), 2, problems)  # the explicit pin and the post-G6 digest
                self.assertTrue(problems[0].startswith("G6: stale: provenance.pins."))
                self.assertTrue(problems[1].startswith("G6: stale: provenance.runtime_versions_digest"))

    def test_post_g6_evidence_with_a_different_digest_is_stale(self):
        evidence_by_id = self.evidence_set(self.fixture("all-eligible"), self.synthetic_versions)
        evidence_by_id["G7"]["provenance"]["runtime_versions_digest"] = "sha256:" + "f" * 64
        problems = self.rules.check_evidence(evidence_by_id, self.synthetic_versions)
        self.assertEqual(len(problems), 1)
        self.assertTrue(problems[0].startswith("G7: stale: provenance.runtime_versions_digest"))
        # A pin that no gate records explicitly still invalidates every digest-bound gate.
        versions = self.changed(self.synthetic_versions, ("harness_module",), "example.invalid/harness@0")
        evidence_by_id = self.evidence_set(self.fixture("all-eligible"), self.synthetic_versions)
        stale = {p.split(":")[0] for p in self.rules.check_evidence(evidence_by_id, versions)}
        self.assertEqual(stale, set(evidence_by_id) - {"G0"})

    def test_evidence_without_provenance_or_under_the_wrong_id_is_rejected(self):
        evidence_by_id = self.evidence_set(self.fixture("all-eligible"), self.synthetic_versions)
        del evidence_by_id["G5"]["provenance"]
        evidence_by_id["G8"]["gate"] = "G7"
        self.assertEqual(
            self.rules.check_evidence(evidence_by_id, self.synthetic_versions),
            ["G5: stale: no provenance", "gates/G8.json records gate 'G7'"],
        )

    def test_checker_cli_reviews_an_evidence_directory(self):
        doc = self.fixture("all-eligible")
        evidence_by_id = self.evidence_set(doc, self.synthetic_versions)
        changed = self.changed(
            self.synthetic_versions, ("sandbox_bases", "claude", "version"), "0.0.1-synthetic"
        )
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            for evidence_id, evidence in evidence_by_id.items():
                (tmp / f"{evidence_id}.json").write_text(json.dumps(evidence), encoding="utf-8")
            (tmp / "old.json").write_text(json.dumps(doc), encoding="utf-8")
            (tmp / "new.json").write_text(json.dumps(self.regenerated(doc, changed)), encoding="utf-8")
            (tmp / "changed.yaml").write_text(json.dumps(changed), encoding="utf-8")
            argv = ["eligibility_rules.py", str(tmp / "old.json"), str(SYNTHETIC_VERSIONS), str(tmp)]
            self.assertEqual(self.rules.main(argv), 0)
            with contextlib.redirect_stdout(io.StringIO()) as out:
                argv = ["eligibility_rules.py", str(tmp / "new.json"), str(tmp / "changed.yaml"), str(tmp)]
                self.assertEqual(self.rules.main(argv), 1)
            self.assertIn("eligibility: G6: stale:", out.getvalue())
            self.assertNotIn("stale: runtime_versions_digest", out.getvalue())

    # --- evidence --------------------------------------------------------------------

    def test_valid_evidence_documents_are_accepted(self):
        for name in VALID_EVIDENCE:
            with self.subTest(fixture=name):
                doc = _load_json(EVIDENCE_FIXTURES / f"{name}.json")
                self.assertEqual([e.message for e in self.evidence.iter_errors(doc)], [])

    def test_invalid_evidence_documents_are_rejected(self):
        for name in INVALID_EVIDENCE:
            with self.subTest(fixture=name):
                doc = _load_json(EVIDENCE_FIXTURES / f"{name}.json")
                self.assertFalse(self.evidence.is_valid(doc))

    def test_no_synthetic_evidence_fixture_records_a_pass(self):
        for path in sorted(EVIDENCE_FIXTURES.glob("*.json")):
            with self.subTest(fixture=path.name):
                self.assertNotEqual(_load_json(path).get("status"), "PASS")

    def test_evidence_rules(self):
        base = _load_json(EVIDENCE_FIXTURES / "valid-g1b-not-run.json")
        cases = {
            "NOT-RUN without its reason": {"not_run_reason": None},
            "PARTIAL outside G11": {"gate": "G0", "status": "PARTIAL", "not_run_reason": None},
            "per-backend gate without backends": {"gate": "G9", "status": "FAIL"},
            "secret-like versions key": {"status": "FAIL", "versions": {"api_token": "x"}},
            "post-G6 gate with G0-style pins": {"provenance": {"pins": {}}},
            "malformed digest": {"provenance": {"runtime_versions_digest": "sha256:xyz"}},
            "extra provenance key": {
                "provenance": {"runtime_versions_digest": "sha256:" + "0" * 64, "note": "x"}
            },
        }
        for label, change in cases.items():
            with self.subTest(case=label):
                doc = {**copy.deepcopy(base), **change}
                self.assertFalse(self.evidence.is_valid(doc))

    def test_evidence_provenance_pass_rules(self):
        evidence_by_id = self.evidence_set(self.fixture("all-eligible"), self.synthetic_versions)
        cases = {
            "G0 PASS with a null sbx pin": ("G0", ("pins", "sbx")),
            "G0 PASS with a null config version": ("G0", ("pins", "docker_agent_config_version")),
            "G6 PASS with a null artifact": ("G6", ("pins", "docker_agent_artifact_sha256")),
            "G6 PASS with a null claude base": ("G6", ("pins", "sandbox_bases", "claude", "base")),
            "G6 PASS with a null codex version": ("G6", ("pins", "sandbox_bases", "codex", "version")),
        }
        for label, (gate, path) in cases.items():
            with self.subTest(case=label):
                evidence = copy.deepcopy(evidence_by_id[gate])
                self.assertTrue(self.evidence.is_valid(evidence))
                target = evidence["provenance"]
                for key in path[:-1]:
                    target = target[key]
                target[path[-1]] = None
                self.assertFalse(self.evidence.is_valid(evidence))
                evidence["status"] = "FAIL"  # a failed gate may not have established the pin
                self.assertTrue(self.evidence.is_valid(evidence))
        g6_without_digest = copy.deepcopy(evidence_by_id["G6"])
        del g6_without_digest["provenance"]["runtime_versions_digest"]
        self.assertFalse(self.evidence.is_valid(g6_without_digest))


if __name__ == "__main__":
    unittest.main()
