"""Contract tests for the G9 recorder (tasks.md T023, gates/G9/record.py).

Deterministic and offline: synthetic captures in a temporary directory, no sandbox, no sbx command,
no network, and no change to the committed pins or to the accepted G1a/G1b/G2/G3/G4 evidence.

G9 is the last security gate before architectural eligibility, so the tests concentrate on the ways
its verdict could be dishonest:

  * a PASS recorded without a working positive control, which would make the gate vacuous;
  * a workload that reproduced the capability being recorded as anything but FAIL;
  * an unavailable backend recorded as PASS rather than NOT-RUN;
  * a PASS being read as untrusted eligibility, which it never is while G1b and G2 are FAILs;
  * credential material reaching the evidence.
"""

import importlib.util
import json
import shutil
import tempfile
import unittest
from pathlib import Path

import jsonschema

ROOT = Path(__file__).resolve().parents[2]
GATES = ROOT / "gates"
STAMP = "2026-09-21T06:00:00Z"
MARKER = "DCA-G9-OK"


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def agent_stream(answer=MARKER, include=True):
    events = [{"type": "team_info", "agent_name": "root", "available_agents": [{"name": "root"}]},
              {"type": "stream_started", "agent_name": "root", "session_id": "s1"}]
    if include:
        events.append({"type": "agent_choice", "agent_name": "root", "session_id": "s1",
                       "content": answer})
    events.append({"type": "stream_stopped", "agent_name": "root", "session_id": "s1",
                   "reason": "normal"})
    return "".join(json.dumps(e) + "\n" for e in events)


class G9Recorder(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.rec = _load("g9_record", GATES / "G9" / "record.py")
        cls.oracle = cls.rec.oracle
        cls.claude = cls.rec.claude
        cls.schema = json.loads((GATES / "evidence.schema.json").read_text(encoding="utf-8"))
        cls.versions = json.loads((ROOT / "runtime" / "versions.yaml").read_text(encoding="utf-8"))
        cls.probes = cls.oracle.load_probes(GATES / "G9" / "probes.json")

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.work = self.tmp / "work"
        self.work.mkdir()
        self._write_work()

    def _write(self, name, document):
        (self.work / name).write_text(
            document if isinstance(document, str) else json.dumps(document), encoding="utf-8")

    def _hosts(self, backend):
        return "api.anthropic.com" if backend == "claude" else "chatgpt.com"

    def _rate_limited(self, backend, privilege):
        """Every attempt 429 - the shape both live Claude runs produced."""
        probe = next(p for p in self.probes if p["backend"] == backend)
        lines = [{"probe": None, "variant": "discovery", "privilege": privilege,
                  "transport": "ok", "candidates": 3, "secrets_excluded": 0, "locations": []}]
        for variant in ("A", "B", "C", "D"):
            lines.append({"probe": probe["id"], "variant": variant, "privilege": privilege,
                          "transport": "ok", "status": 429, "content_type": "application/json",
                          "body_bytes": 114, "pattern_ids": [], "position": None})
        return "".join(json.dumps(l) + "\n" for l in lines)

    def _workload(self, backend, privilege, mediated=None, ambiguous=None, drop=None,
                  include_discovery=True):
        """A full, clean workload capture, optionally corrupted in one specific way."""
        probe = next(p for p in self.probes if p["backend"] == backend)
        pid = probe["expected_unauthenticated"]["pattern_id"]
        lines = []
        if include_discovery:
            lines.append({"probe": None, "variant": "discovery", "privilege": privilege,
                          "transport": "ok", "candidates": 2, "secrets_excluded": 1,
                          "locations": ["environment:OPENAI_API_KEY", "proc-environ:HTTPS_PROXY"]})
        for variant in ("A", "B", "C", "D"):
            if drop == variant:
                continue
            entry = {"probe": probe["id"], "variant": variant, "privilege": privilege,
                     "transport": "ok", "status": 401, "content_type": "application/json",
                     "body_bytes": 141, "pattern_ids": [pid], "position": None}
            if mediated == variant:
                signal = probe["mediation_signal"]["any_of"][0]
                entry.update(status=signal["statuses"][0], pattern_ids=[signal["pattern_id"]])
            if ambiguous == variant:
                entry.update(status=503, pattern_ids=[])
            lines.append(entry)
        return "".join(json.dumps(l) + "\n" for l in lines)

    def _write_work(self):
        pins = self.versions
        self._write("pf-version.json", {
            "client": {"version": pins["sbx"]["exact"]},
            "server": {"version": pins["sbx"]["exact"], "state": "running"}})
        self._write("pf-ssh-forwarding.json", {"key": "ssh.agentForwardingEnabled", "value": False})
        self._write("pf-ssh-socket.json", {"key": "ssh.agentSocketPath", "value": ""})
        baseline = {"rules": [dict(self.claude.preflight_module.BOOTSTRAP_RULE,
                                   name="default-deny-all", editable=False)]}
        accepted, why = self.claude.accepted_network_state(versions=self.versions)
        self.assertIsNone(why, why)
        self._write("pf-policy.json", baseline)
        self._write("policy-after.json", baseline)
        self._write("pf-ls.json", {"sandboxes": []})
        self._write("ls-after.json", {"sandboxes": []})
        governance = {"allowed": False, "deny_kind": "implicit",
                      "resource_value": self.claude.GOVERNANCE_PROBE,
                      "target": self.claude.GOVERNANCE_PROBE, "governance": {"active": False}}
        self._write("governance-before.json", governance)
        self._write("governance-after.json", governance)

        images = []
        for backend, module in (("claude", self.claude), ("codex", self.rec.codex)):
            image, digest = (module.claude_base(self.versions) if backend == "claude"
                             else module.codex_base(self.versions))
            repository, _, tag = image.rpartition(":")
            images.append({"repository": f"docker.io/{repository}", "tag": tag,
                           "id": digest.removeprefix("sha256:")[:12]})
        self._write("templates.json", {"images": images})

        for backend in ("claude", "codex"):
            self._write(f"vmstate-{backend}.txt",
                        "stdin_is_tty=no\nworkload_user=agent\nsudo_resolves_to=root\n"
                        f"agent_binary={self.versions['docker_agent']}\n")
            self._write(f"control-{backend}.json", agent_stream())
            # the exact shape pinned sbx v0.43.0 emits, taken from a real capture
            self._write(f"policylog-control-{backend}.json",
                        {"allowed_hosts": [{"host": f"{self._hosts(backend)}:443",
                                            "vm_name": f"dca-g9-{backend}",
                                            "proxy_type": "forward", "count_since": 30}],
                         "blocked_hosts": [{"host": "api.docker.com:443",
                                            "vm_name": f"dca-g9-{backend}",
                                            "count_since": 3}]})
            for privilege in ("user", "sudo"):
                self._write(f"workload-{privilege}-{backend}.jsonl",
                            self._workload(backend, privilege))
            self._write(f"ls-before-{backend}.json", {"sandboxes": []})
            self._write(f"ls-between-{backend}.json", {"sandboxes": []})

    def _capture(self, **overrides):
        obs = {
            "sbx_env_ssh_auth_sock": "removed",
            "pf_version_exit": "0", "pf_ssh_forwarding_exit": "0", "pf_ssh_socket_exit": "0",
            "pf_policy_exit": "0", "pf_ls_exit": "0",
            "governance_before_exit": "1", "governance_after_exit": "1",
            "ls_after_exit": "0", "policy_after_exit": "0", "templates_exit": "0",
            "available_backends": "claude codex",
        }
        for backend, module in (("claude", self.claude), ("codex", self.rec.codex)):
            allow, deny = (module.claude_policy("trusted") if backend == "claude"
                           else module.codex_policy("trusted"))
            image = (module.claude_base(self.versions)[0] if backend == "claude"
                     else module.codex_base(self.versions)[0])
            obs.update({
                f"probe_host_{backend}": self._hosts(backend),
                f"sbx_resolved_base_{backend}": image,
                f"policy_allow_{backend}": ",".join(allow),
                f"policy_deny_{backend}": ",".join(deny),
                f"policy_rules_{backend}_exit": "0",
                f"safety_flag_{backend}": "--safety strict" if backend == "codex" else "",
                f"create_dca-g9-{backend}_exit": "0", f"rm_dca-g9-{backend}_exit": "0",
                f"vmstate_{backend}_exit": "0", f"control_{backend}_exit": "0",
                f"policylog_control_{backend}_exit": "0",
                f"workload_user_{backend}_exit": "0", f"workload_sudo_{backend}_exit": "0",
            })
        obs.update(overrides)
        return obs

    def _record_to(self, obs=None):
        obs = obs or self._capture()
        obs_path = self.tmp / "observations.env"
        obs_path.write_text("".join(f"{k}={v}\n" for k, v in obs.items()), encoding="utf-8")
        evidence_path = self.tmp / "G9.json"
        evidence, facts = self.rec.record(
            str(obs_path), str(self.work), str(ROOT / "runtime" / "versions.yaml"),
            str(evidence_path))
        return evidence, facts

    def _results(self, obs=None, backend="claude"):
        _, per_backend, statuses, _, _ = self.rec.evaluate(
            obs or self._capture(), str(self.work), self.versions)
        return ({row["id"]: row["result"] for row in per_backend[backend]}, statuses[backend][0])

    def _validate(self, evidence):
        self.assertEqual(
            [e.message for e in
             jsonschema.Draft202012Validator(self.schema).iter_errors(evidence)], [])

    # --- the clean path ---------------------------------------------------------------------------

    def test_01_a_clean_capture_passes_both_backends(self):
        evidence, _ = self._record_to()
        self._validate(evidence)
        for backend in ("claude", "codex"):
            with self.subTest(backend=backend):
                self.assertEqual(evidence["backends"][backend]["status"], "PASS", json.dumps(
                    [c for c in evidence["backends"][backend]["criteria"]
                     if c["result"] != "PASS"], indent=2))
        self.assertEqual(evidence["status"], "PASS")

    def test_02_a_pass_never_claims_untrusted_eligibility(self):
        """G9 alone never makes a backend untrusted-eligible while G1b and G2 are FAILs."""
        evidence, _ = self._record_to()
        notes = evidence["notes"]
        self.assertIn("TRUSTED-ONLY", notes)
        self.assertIn("G1b", notes)
        self.assertIn("G2", notes)
        self.assertIn("S5a", notes)
        self.assertIn("split-plane", notes)

    def test_03_no_credential_material_appears_in_the_evidence(self):
        import re
        text = json.dumps(self._record_to()[0])
        for label, pattern in {
            "jwt": r"\beyJ[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}\.",
            "anthropic key": r"\bsk-ant-[a-z]{3}[0-9]{2}-",
            "openai key": r"\bsk-[A-Za-z0-9]{24,}",
            "bearer value": r"[Bb]earer\s+[A-Za-z0-9._\-]{12,}",
            "oauth json value": r'"(access_token|refresh_token|id_token)"\s*:\s*"[^"]{8,}"',
        }.items():
            with self.subTest(pattern=label):
                self.assertIsNone(re.search(pattern, text), label)

    # --- the positive control ---------------------------------------------------------------------

    def test_04_a_failed_positive_control_fails_the_backend(self):
        _, status = self._results(self._capture(**{"control_claude_exit": "1"}))
        self.assertEqual(status, "FAIL")

    def test_05_a_control_that_produced_no_answer_fails(self):
        self._write("control-claude.json", agent_stream(include=False))
        results, status = self._results()
        self.assertEqual(results["G9.control.claude"], "FAIL")
        self.assertEqual(status, "FAIL")

    def test_06_a_control_without_the_probe_host_in_the_policy_log_fails(self):
        self._write("policylog-control-claude.json",
                    {"allowed_hosts": [{"host": "pypi.org:443", "count_since": 2}],
                     "blocked_hosts": []})
        results, _ = self._results()
        self.assertEqual(results["G9.control.claude"], "FAIL")

    def test_07_a_blocked_or_untravelled_policy_log_entry_is_not_a_control(self):
        """Blocked traffic, or a permitting rule with no traffic, is not evidence of a call."""
        for log in ({"allowed_hosts": [], "blocked_hosts": [
                        {"host": "api.anthropic.com:443", "count_since": 4}]},
                    {"allowed_hosts": [{"host": "api.anthropic.com:443", "count_since": 0}],
                     "blocked_hosts": []},
                    {"allowed_hosts": [], "blocked_hosts": []}):
            with self.subTest(log=str(log)[:48]):
                self._write("policylog-control-claude.json", log)
                self.assertEqual(self._results()[0]["G9.control.claude"], "FAIL")

    def test_07b_the_parser_reads_the_real_captured_policy_log_shape(self):
        """Regression: the first implementation guessed generic key names, found nothing, and
        failed both positive controls although the agents had genuinely called the endpoints."""
        self._write("policylog-real.json", {
            "allowed_hosts": [{"host": "api.anthropic.com:443", "vm_name": "dca-g9-claude",
                               "proxy_type": "forward", "count_since": 30}],
            "blocked_hosts": [{"host": "mcp-proxy.anthropic.com:443", "count_since": 2}]})
        hosts = self.rec.policy_log_hosts(str(self.work), "policylog-real.json")
        self.assertIs(hosts.get("api.anthropic.com"), True)
        self.assertIs(hosts.get("mcp-proxy.anthropic.com"), False)

    # --- the workload -----------------------------------------------------------------------------

    def test_08_a_variant_that_reproduced_the_capability_fails(self):
        """The finding G9 exists to catch, at either privilege level."""
        for privilege in ("user", "sudo"):
            with self.subTest(privilege=privilege):
                self._write(f"workload-{privilege}-claude.jsonl",
                            self._workload("claude", privilege, mediated="B"))
                results, status = self._results()
                self.assertEqual(results["G9.probe.claude-anthropic-messages"], "FAIL")
                self.assertEqual(status, "FAIL")
                self.setUp()

    def test_09_an_ambiguous_variant_fails(self):
        self._write("workload-sudo-claude.jsonl",
                    self._workload("claude", "sudo", ambiguous="C"))
        self.assertEqual(self._results()[0]["G9.probe.claude-anthropic-messages"], "FAIL")

    def test_10_a_missing_required_variant_fails(self):
        self._write("workload-user-claude.jsonl", self._workload("claude", "user", drop="A"))
        self.assertEqual(self._results()[0]["G9.probe.claude-anthropic-messages"], "FAIL")

    def test_11_a_missing_sudo_workload_fails(self):
        (self.work / "workload-sudo-claude.jsonl").unlink()
        results, status = self._results()
        self.assertEqual(results["G9.probe.claude-anthropic-messages"], "FAIL")
        self.assertEqual(status, "FAIL")

    def test_12_a_workload_that_exited_non_zero_fails(self):
        for privilege in ("user", "sudo"):
            with self.subTest(privilege=privilege):
                results, _ = self._results(
                    self._capture(**{f"workload_{privilege}_claude_exit": "1"}))
                self.assertEqual(results["G9.workload.claude"], "FAIL")

    def test_13_a_workload_with_no_discovery_pass_fails(self):
        self._write("workload-user-claude.jsonl",
                    self._workload("claude", "user", include_discovery=False))
        self.assertEqual(self._results()[0]["G9.workload.claude"], "FAIL")

    # --- privilege and setup ------------------------------------------------------------------------

    def test_14_a_workload_whose_sudo_is_not_root_fails(self):
        """An unprivileged-only failure does not answer T023's question."""
        self._write("vmstate-claude.txt",
                    "stdin_is_tty=no\nworkload_user=agent\nsudo_resolves_to=agent\n"
                    f"agent_binary={self.versions['docker_agent']}\n")
        self.assertEqual(self._results()[0]["G9.setup.claude"], "FAIL")

    def test_15_codex_without_strict_on_the_control_fails(self):
        for flag in ("", "--safety balanced", "strict"):
            with self.subTest(flag=flag):
                results, _ = self._results(
                    self._capture(**{"safety_flag_codex": flag}), backend="codex")
                self.assertEqual(results["G9.setup.codex"], "FAIL")

    def test_16_a_base_mismatch_fails(self):
        results, _ = self._results(
            self._capture(**{"sbx_resolved_base_claude": "docker/sandbox-templates:wrong"}))
        self.assertEqual(results["G9.base"], "FAIL")

    # --- availability and independence ----------------------------------------------------------------

    def test_17_an_unavailable_backend_is_not_run_not_pass(self):
        original = self.rec.availability
        self.rec.availability = lambda b, v: ((False, "G3 is 'FAIL', not PASS") if b == "codex"
                                              else original(b, v))
        self.addCleanup(setattr, self.rec, "availability", original)
        evidence, _ = self._record_to()
        self.assertEqual(evidence["backends"]["codex"]["status"], "NOT-RUN")
        self.assertNotEqual(evidence["backends"]["codex"]["status"], "PASS")
        self.assertTrue(evidence["backends"]["codex"]["not_run_reason"])
        self.assertEqual(evidence["backends"]["claude"]["status"], "PASS")
        self._validate(evidence)

    def test_18_stale_upstream_evidence_makes_a_backend_not_run(self):
        original = self.claude.rules_module.evidence_problems
        self.claude.rules_module.evidence_problems = lambda e, v: (
            ["docker_agent pin drifted"] if e.get("gate") == "G1a" else original(e, v))
        self.addCleanup(setattr, self.claude.rules_module, "evidence_problems", original)
        evidence, _ = self._record_to()
        self.assertEqual(evidence["backends"]["claude"]["status"], "NOT-RUN")
        self.assertIn("stale", evidence["backends"]["claude"]["not_run_reason"])

    def test_19_one_backend_failing_does_not_fail_the_other(self):
        self._write("workload-sudo-claude.jsonl",
                    self._workload("claude", "sudo", mediated="B"))
        evidence, _ = self._record_to()
        self.assertEqual(evidence["backends"]["claude"]["status"], "FAIL")
        self.assertEqual(evidence["backends"]["codex"]["status"], "PASS")
        self.assertEqual(evidence["status"], "FAIL")
        self._validate(evidence)

    def test_20_cleanup_and_fingerprint_are_still_enforced(self):
        self._write("ls-after.json", {"sandboxes": [{"name": "dca-g9-claude"}]})
        criteria, _, _, _, _ = self.rec.evaluate(self._capture(), str(self.work), self.versions)
        self.assertEqual({c["id"]: c["result"] for c in criteria}["G9.clean"], "FAIL")
        self.setUp()
        self._write("policy-after.json", {"rules": []})
        criteria, _, _, _, _ = self.rec.evaluate(self._capture(), str(self.work), self.versions)
        self.assertEqual({c["id"]: c["result"] for c in criteria}["G9.fingerprint"], "FAIL")


    # --- the semantic distinction: ambiguous FAIL is not a reproduced capability -------------------

    def test_21_a_rate_limited_probe_fails_closed_as_inconclusive_not_as_a_breach(self):
        """A 429 on every variant is a FAIL, but it must say WHY in machine-readable form.

        Both live Claude runs produced exactly this: no request ever returned a completion, so
        nothing was reproduced - yet nothing was proven either, because the provider would not
        distinguish callers. Reporting that as a bare FAIL invites the reader to assume the
        workload got in, which is the opposite of what happened.
        """
        for privilege in ("user", "sudo"):
            self._write(f"workload-{privilege}-claude.jsonl",
                        self._rate_limited("claude", privilege))
        evidence, _ = self._record_to()
        claude = evidence["backends"]["claude"]

        self.assertEqual(claude["status"], "FAIL", "ambiguity must still fail CLOSED")
        self.assertEqual(claude["failure_reason"], "ambiguous_provider_response")
        self.assertEqual(claude["ambiguity_reason"], "rate_limited")
        self.assertEqual(claude["capability_reproduced"], "not_observed")
        self.assertEqual(claude["capability_non_usability"], "not_proven")
        self._validate(evidence)

    def test_22_an_ambiguous_fail_never_claims_the_capability_was_reproduced(self):
        for privilege in ("user", "sudo"):
            self._write(f"workload-{privilege}-claude.jsonl",
                        self._rate_limited("claude", privilege))
        evidence, _ = self._record_to()
        claude = evidence["backends"]["claude"]
        text = json.dumps(claude) + evidence["notes"]

        self.assertNotIn("capability_reproduced=observed", text)
        self.assertNotIn('"capability_reproduced": "observed"', text)
        self.assertIn("capability_reproduced=not_observed", evidence["notes"])
        self.assertIn("INCONCLUSIVE", evidence["notes"])
        # every probe criterion leads with the two typed facts, so no excerpt can mislead
        for row in claude["criteria"]:
            if row["id"].startswith("G9.probe."):
                self.assertTrue(row["evidence_ref"].startswith("capability_reproduced=not_observed"))

    def test_23_a_genuinely_reproduced_capability_is_reported_differently(self):
        """The distinction has to cut both ways or it is not a distinction."""
        self._write("workload-sudo-claude.jsonl",
                    self._workload("claude", "sudo", mediated="B"))
        evidence, _ = self._record_to()
        claude = evidence["backends"]["claude"]
        self.assertEqual(claude["status"], "FAIL")
        self.assertEqual(claude["failure_reason"], "capability_reproduced")
        self.assertEqual(claude["capability_reproduced"], "observed")
        self.assertEqual(claude["capability_non_usability"], "not_proven")
        self.assertIsNone(claude["ambiguity_reason"])
        self._validate(evidence)

    def test_24_a_pass_proves_non_usability_and_says_so(self):
        evidence, _ = self._record_to()
        for backend in ("claude", "codex"):
            with self.subTest(backend=backend):
                entry = evidence["backends"][backend]
                self.assertEqual(entry["status"], "PASS")
                self.assertEqual(entry["capability_non_usability"], "proven")
                self.assertEqual(entry["capability_reproduced"], "not_observed")
                self.assertIsNone(entry["failure_reason"])

    def test_25_the_status_vocabulary_was_not_expanded(self):
        """No INCONCLUSIVE status was introduced; the distinction lives in typed reason fields."""
        allowed = set(self.schema["$defs"]["status"]["enum"])
        self.assertEqual(allowed, {"PASS", "FAIL", "PARTIAL", "NOT-RUN", "NOT-APPLICABLE"})
        for privilege in ("user", "sudo"):
            self._write(f"workload-{privilege}-claude.jsonl",
                        self._rate_limited("claude", privilege))
        evidence, _ = self._record_to()
        self.assertIn(evidence["backends"]["claude"]["status"], allowed)
        self.assertEqual(evidence["status"], "FAIL")

    def test_26_trusted_only_implications_survive_either_reason(self):
        for privilege in ("user", "sudo"):
            self._write(f"workload-{privilege}-claude.jsonl",
                        self._rate_limited("claude", privilege))
        notes = self._record_to()[0]["notes"]
        for required in ("TRUSTED-ONLY", "G1b", "G2", "S5a", "split-plane"):
            self.assertIn(required, notes)


if __name__ == "__main__":
    unittest.main()
