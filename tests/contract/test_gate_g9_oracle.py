"""Contract tests for the G9 oracle (tasks.md T023, gates/G9/oracle.py + gates/G9/probes.json).

Deterministic and offline: no sandbox, no sbx command, no network. S5b (T088) reuses this oracle
exactly, so these tests are the specification of its behaviour rather than a smoke test.

G9 proves a NEGATIVE - that a repository workload cannot reproduce the agent's authenticated
control-plane capability - so the tests concentrate on every way a negative could be claimed
without being earned:

  * a 401 that was never compared against a working positive control;
  * a result that cannot separate an authenticated caller from an unauthenticated one;
  * a transport failure, a redirect or a server error read as "cannot authenticate";
  * a missing variant or a missing privilege level quietly passing.
"""

import importlib.util
import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
GATES = ROOT / "gates"


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


oracle = _load("g9_oracle", GATES / "G9" / "oracle.py")
PROBES = oracle.load_probes(GATES / "G9" / "probes.json")
CLAUDE = next(p for p in PROBES if p["backend"] == "claude")
CODEX = next(p for p in PROBES if p["backend"] == "codex")


def obs(variant, privilege, status=401, pattern_ids=(), transport="ok",
        content_type="application/json", position=None):
    return {"variant": variant, "privilege": privilege, "transport": transport,
            "status": status, "content_type": content_type, "body_bytes": 141,
            "pattern_ids": list(pattern_ids), "position": position}


def all_unauthenticated(probe):
    pid = probe["expected_unauthenticated"]["pattern_id"]
    return [obs(v, p, 401, [pid]) for v in ("A", "B", "C", "D") for p in ("user", "sudo")]


GOOD_CONTROL = {"result_class": oracle.MEDIATED, "why": "agent answered",
                "policy_log_host": "api.anthropic.com"}


class G9ProbeDefinitions(unittest.TestCase):
    def test_01_one_probe_per_available_backend_control_plane_host(self):
        self.assertEqual({p["backend"] for p in PROBES}, {"claude", "codex"})
        self.assertEqual(CLAUDE["host"], "api.anthropic.com")
        self.assertEqual(CODEX["host"], "chatgpt.com")

    def test_02_every_probe_declares_the_fields_t023_requires(self):
        for probe in PROBES:
            with self.subTest(probe=probe["id"]):
                for field in ("backend", "host", "endpoint", "request_family",
                              "expected_unauthenticated", "mediation_signal",
                              "interpretation_source", "credential_positions"):
                    self.assertIn(field, probe)
                self.assertTrue(probe["expected_unauthenticated"]["pattern_id"])
                self.assertTrue(probe["mediation_signal"]["any_of"])
                self.assertGreaterEqual(len(probe["interpretation_source"]), 2)

    def test_03_the_discriminator_is_structural_not_a_bare_status(self):
        """A status code alone must never decide the classification.

        Either form of structural discriminator satisfies this - exact field values, or the
        presence of the provider's rejection key - but a probe carrying neither would be deciding
        on the status alone, which T023 forbids.
        """
        for probe in PROBES:
            with self.subTest(probe=probe["id"]):
                unauth = probe["expected_unauthenticated"]
                self.assertTrue(unauth.get("predicate") or unauth.get("any_key"),
                                "the probe would be classifying on the bare status code")
                # the same 401 with no matching body shape is ambiguous, not unauthenticated
                result, _, _ = oracle.classify(probe, obs("A", "user", 401, []))
                self.assertEqual(result, oracle.AMBIGUOUS)


class G9Classification(unittest.TestCase):
    def test_04_the_documented_unauthenticated_body_classifies_unauthenticated(self):
        for probe in PROBES:
            with self.subTest(probe=probe["id"]):
                pid = probe["expected_unauthenticated"]["pattern_id"]
                result, pattern, _ = oracle.classify(probe, obs("A", "user", 401, [pid]))
                self.assertEqual(result, oracle.UNAUTHENTICATED)
                self.assertEqual(pattern, pid)

    def test_05_a_mediation_signal_classifies_mediated(self):
        for probe in PROBES:
            for signal in probe["mediation_signal"]["any_of"]:
                with self.subTest(probe=probe["id"], pattern=signal["pattern_id"]):
                    status = signal["statuses"][0]
                    result, pattern, _ = oracle.classify(
                        probe, obs("B", "sudo", status, [signal["pattern_id"]]))
                    self.assertEqual(result, oracle.MEDIATED)
                    self.assertEqual(pattern, signal["pattern_id"])

    def test_06_transport_failures_are_ambiguous_never_unauthenticated(self):
        for transport in ("timeout", "tls-error", "url-error", "error", "not-applicable"):
            with self.subTest(transport=transport):
                result, _, why = oracle.classify(
                    CLAUDE, obs("A", "user", None, [], transport=transport))
                self.assertEqual(result, oracle.AMBIGUOUS)
                self.assertIn("did not complete", why)

    def test_07_redirects_and_server_errors_are_ambiguous(self):
        for status in (301, 302, 307, 308):
            with self.subTest(status=status):
                self.assertEqual(oracle.classify(CLAUDE, obs("A", "user", status, []))[0],
                                 oracle.AMBIGUOUS)
        for status in (500, 502, 503, 504):
            with self.subTest(status=status):
                self.assertEqual(oracle.classify(CLAUDE, obs("A", "user", status, []))[0],
                                 oracle.AMBIGUOUS)

    def test_08_an_indistinguishable_body_is_ambiguous(self):
        """A 401 whose body is not the provider's documented shape proves nothing."""
        for status in (401, 403, 200):
            with self.subTest(status=status):
                result, pattern, why = oracle.classify(
                    CLAUDE, obs("A", "sudo", status, [], content_type="text/html"))
                self.assertEqual(result, oracle.AMBIGUOUS)
                self.assertIsNone(pattern)
                self.assertIn("cannot be told apart", why)

    def test_09_match_patterns_reads_the_real_provider_bodies(self):
        """The in-VM matcher, against the exact bodies observed during probe definition."""
        anthropic = json.dumps({"type": "error", "request_id": "req_1",
                                "error": {"type": "authentication_error", "message": "x"}}).encode()
        self.assertEqual(oracle.match_patterns(CLAUDE, 401, "application/json", anthropic),
                         ["anthropic-authentication-error"])
        chatgpt = json.dumps({"detail": "Unauthorized"}).encode()
        self.assertEqual(oracle.match_patterns(CODEX, 401, "application/json", chatgpt),
                         ["chatgpt-401-detail-rejection"])

    def test_10_match_patterns_detects_a_real_authenticated_result(self):
        message = json.dumps({"type": "message", "role": "assistant", "id": "msg_1"}).encode()
        self.assertIn("anthropic-message-object",
                      oracle.match_patterns(CLAUDE, 200, "application/json", message))
        response = json.dumps({"object": "response", "id": "resp_1"}).encode()
        self.assertIn("chatgpt-response-object",
                      oracle.match_patterns(CODEX, 200, "application/json", response))
        self.assertIn("chatgpt-response-stream",
                      oracle.match_patterns(CODEX, 200, "text/event-stream", b"data: {}"))

    def test_11_match_patterns_never_confuses_the_two_classes(self):
        unauth = json.dumps({"detail": "Unauthorized"}).encode()
        matched = oracle.match_patterns(CODEX, 401, "application/json", unauth)
        mediation = {s["pattern_id"] for s in CODEX["mediation_signal"]["any_of"]}
        self.assertFalse(set(matched) & mediation)

    def test_11b_all_three_real_chatgpt_rejection_bodies_are_unauthenticated(self):
        """Regression: the first live run classified two of the provider's three 401 shapes as
        AMBIGUOUS, because the probe pinned one exact message instead of the rejection SHAPE."""
        for label, body in (
                ("no credential", {"detail": "Unauthorized"}),
                ("unparseable bearer", {"detail": "Could not parse your authentication token."}),
                ("bad account id", {"detail": {"code": "missing_end_user_auth"}, "error": {}})):
            with self.subTest(case=label):
                matched = oracle.match_patterns(
                    CODEX, 401, "application/json", json.dumps(body).encode())
                self.assertEqual(matched, ["chatgpt-401-detail-rejection"])
                self.assertEqual(
                    oracle.classify(CODEX, obs("B", "sudo", 401, matched))[0],
                    oracle.UNAUTHENTICATED)

    def test_11c_a_401_without_the_provider_rejection_shape_stays_ambiguous(self):
        """The shape requirement keeps this narrower than a bare status code."""
        for ct, body in (("text/html", b"<html>401</html>"), ("application/json", b"{}"),
                         ("application/json", b"[]")):
            with self.subTest(ct=ct):
                matched = oracle.match_patterns(CODEX, 401, ct, body)
                self.assertEqual(matched, [])
                self.assertEqual(oracle.classify(CODEX, obs("A", "user", 401, matched))[0],
                                 oracle.AMBIGUOUS)

    def test_11d_rate_limiting_is_never_a_mediation_signal(self):
        """Regression, and the defect that manufactured a Claude FAIL in the first live run.

        Variant A sends no credential at all and still received 429 rate_limit_error, so Anthropic
        applies rate limiting before authentication. Treating it as proof of an authenticated
        result made the workload look like it had reproduced the capability.
        """
        body = json.dumps({"type": "error", "error": {"type": "rate_limit_error"}}).encode()
        matched = oracle.match_patterns(CLAUDE, 429, "application/json", body)
        self.assertEqual(matched, [])
        result, pattern, _ = oracle.classify(CLAUDE, obs("A", "user", 429, matched))
        self.assertEqual(result, oracle.AMBIGUOUS)
        self.assertNotEqual(result, oracle.MEDIATED)
        mediation = {s["pattern_id"] for s in CLAUDE["mediation_signal"]["any_of"]}
        self.assertNotIn("anthropic-rate-limit-error", mediation)
        self.assertNotIn("anthropic-permission-error", mediation)

    def test_11e_only_a_real_completion_proves_an_authenticated_result(self):
        """Every mediation pattern on every probe must require a 2xx success, never an error."""
        for probe in PROBES:
            for signal in probe["mediation_signal"]["any_of"]:
                with self.subTest(probe=probe["id"], pattern=signal["pattern_id"]):
                    self.assertTrue(all(200 <= s < 300 for s in signal["statuses"]),
                                    "an error status can be produced before authentication")

    def test_12_a_non_json_or_empty_body_matches_nothing(self):
        for body in (b"", b"<html>nope</html>", b"{not json", b"null"):
            with self.subTest(body=body[:12]):
                self.assertEqual(oracle.match_patterns(CLAUDE, 401, "text/html", body), [])


class G9ProbeEvaluation(unittest.TestCase):
    def test_13_a_valid_control_and_all_unauthenticated_variants_passes(self):
        holds, problems, details = oracle.evaluate_probe(
            CLAUDE, GOOD_CONTROL, all_unauthenticated(CLAUDE))
        self.assertTrue(holds, problems)
        self.assertEqual(details["positive_control"], oracle.MEDIATED)

    def test_14_any_variant_reproducing_the_capability_fails(self):
        """The finding G9 exists to catch."""
        for variant, privilege in (("A", "user"), ("B", "sudo"), ("C", "user"), ("D", "sudo")):
            with self.subTest(variant=variant, privilege=privilege):
                variants = all_unauthenticated(CLAUDE)
                variants.append(obs(variant, privilege, 200, ["anthropic-message-object"]))
                holds, problems, _ = oracle.evaluate_probe(CLAUDE, GOOD_CONTROL, variants)
                self.assertFalse(holds)
                self.assertTrue(any("REPRODUCED" in p for p in problems))

    def test_15_a_missing_positive_control_fails(self):
        holds, problems, _ = oracle.evaluate_probe(CLAUDE, None, all_unauthenticated(CLAUDE))
        self.assertFalse(holds)
        self.assertTrue(any("no positive control" in p for p in problems))

    def test_16_a_failed_or_ambiguous_positive_control_fails(self):
        for control_class in (oracle.UNAUTHENTICATED, oracle.AMBIGUOUS, None):
            with self.subTest(control=control_class):
                control = dict(GOOD_CONTROL, result_class=control_class)
                holds, problems, _ = oracle.evaluate_probe(
                    CLAUDE, control, all_unauthenticated(CLAUDE))
                self.assertFalse(holds)

    def test_17_a_control_without_a_policy_log_entry_fails(self):
        control = dict(GOOD_CONTROL, policy_log_host=None)
        holds, problems, _ = oracle.evaluate_probe(CLAUDE, control, all_unauthenticated(CLAUDE))
        self.assertFalse(holds)
        self.assertTrue(any("policy log" in p for p in problems))

    def test_18_a_401_without_a_valid_control_is_not_a_pass(self):
        """401s everywhere plus no working control is exactly the vacuous result T023 forbids."""
        holds, _, _ = oracle.evaluate_probe(CLAUDE, None, all_unauthenticated(CLAUDE))
        self.assertFalse(holds)

    def test_19_a_missing_required_variant_fails(self):
        for missing in oracle.REQUIRED_VARIANTS:
            with self.subTest(missing=missing):
                variants = [o for o in all_unauthenticated(CLAUDE) if o["variant"] != missing]
                holds, problems, _ = oracle.evaluate_probe(CLAUDE, GOOD_CONTROL, variants)
                self.assertFalse(holds)
                self.assertTrue(any("no completed attempt" in p for p in problems))

    def test_20_a_missing_sudo_run_fails(self):
        variants = [o for o in all_unauthenticated(CLAUDE) if o["privilege"] != "sudo"]
        holds, problems, _ = oracle.evaluate_probe(CLAUDE, GOOD_CONTROL, variants)
        self.assertFalse(holds)
        self.assertTrue(any("'sudo'" in p and "no completed attempt" in p
                            for p in problems))

    def test_21_variant_d_may_be_absent_or_not_applicable_without_failing(self):
        """D only exists where the VM exposes such a representation; A/B/C never get that latitude.

        Regression: an explicit not-applicable D row - emitted when the sandbox has no proxy
        variable and no credential-helper socket - was being CLASSIFIED, came out ambiguous, and
        failed the probe for a representation the VM never had.
        """
        absent = [o for o in all_unauthenticated(CLAUDE) if o["variant"] != "D"]
        holds, problems, _ = oracle.evaluate_probe(CLAUDE, GOOD_CONTROL, absent)
        self.assertTrue(holds, problems)

        marked = absent + [
            dict(obs("D", p), transport=oracle.TRANSPORT_INAPPLICABLE, status=None,
                 note="the VM exposes no proxy environment variable") for p in ("user", "sudo")]
        holds, problems, details = oracle.evaluate_probe(CLAUDE, GOOD_CONTROL, marked)
        self.assertTrue(holds, problems)
        self.assertEqual(len(details["inapplicable"]), 2)

    def test_21b_a_required_variant_that_only_reports_not_applicable_fails(self):
        """Presence is not evidence: a variant whose every record is inapplicable tested nothing."""
        for required in oracle.REQUIRED_VARIANTS:
            with self.subTest(variant=required):
                variants = [o for o in all_unauthenticated(CLAUDE) if o["variant"] != required]
                variants += [dict(obs(required, p), transport=oracle.TRANSPORT_INAPPLICABLE,
                                  status=None) for p in ("user", "sudo")]
                holds, problems, _ = oracle.evaluate_probe(CLAUDE, GOOD_CONTROL, variants)
                self.assertFalse(holds)
                self.assertTrue(any("no completed attempt" in p for p in problems))

    def test_22_an_ambiguous_variant_fails_the_probe(self):
        for bad in (obs("B", "sudo", None, [], transport="timeout"),
                    obs("B", "sudo", 503, []),
                    obs("B", "sudo", 302, []),
                    obs("B", "sudo", 401, [], content_type="text/html")):
            with self.subTest(status=bad["status"], transport=bad["transport"]):
                holds, problems, _ = oracle.evaluate_probe(
                    CLAUDE, GOOD_CONTROL, all_unauthenticated(CLAUDE) + [bad])
                self.assertFalse(holds)
                self.assertTrue(any("ambiguous" in p for p in problems))


class G9BackendEvaluation(unittest.TestCase):
    def test_23_a_backend_with_no_probe_fails_rather_than_passes_vacuously(self):
        status, results = oracle.evaluate_backend([], {}, {})
        self.assertEqual(status, "FAIL")

    def test_24_a_backend_passes_only_when_every_probe_passes(self):
        good = {CLAUDE["id"]: all_unauthenticated(CLAUDE)}
        status, _ = oracle.evaluate_backend([CLAUDE], {CLAUDE["id"]: GOOD_CONTROL}, good)
        self.assertEqual(status, "PASS")
        bad = {CLAUDE["id"]: all_unauthenticated(CLAUDE)
               + [obs("B", "sudo", 200, ["anthropic-message-object"])]}
        status, _ = oracle.evaluate_backend([CLAUDE], {CLAUDE["id"]: GOOD_CONTROL}, bad)
        self.assertEqual(status, "FAIL")


if __name__ == "__main__":
    unittest.main()
