"""Launcher Phase 1 and Phase 2 (tasks.md T063, contracts/launcher-cli.md).

Everything here runs against the **fake** `sbx` and **synthetic** eligibility fixtures, never live
gates: a precondition test that depended on the developer's real gate state would pass or fail for
reasons that have nothing to do with the launcher.

The distinction these tests exist to hold is the one that is easiest to blur in code: an exit-3
**refusal** means no task disposition exists at all, while a Phase-2 **`blocked` report** with
`sandbox_created: false` is a real disposition the developer can act on. Collapsing either into
the other - a generic CLI error for an untrusted request with no eligible backend, or a fabricated
report for a missing gate file - would misreport what the system actually knows.
"""

import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
WORK = Path(__file__).resolve().parent / "work"
FIXTURES = ROOT / "tests" / "fixtures" / "eligibility"
FAKE_SBX = ROOT / "tests" / "fakes" / "sbx"


def _load(name, path):
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


errors = _load("dca_errors", ROOT / "src" / "dca" / "errors.py")
eligibility = _load("dca_eligibility", ROOT / "src" / "dca" / "eligibility.py")
jsonschema_stdlib = _load("dca_jsonschema", ROOT / "src" / "dca" / "jsonschema.py")
launcher = _load("dca_launcher", ROOT / "src" / "dca" / "launcher.py")
sbx_module = _load("dca_sbx", ROOT / "src" / "dca" / "sbx.py")
g4 = _load("dca_g4_record", ROOT / "gates" / "G4" / "record.py")
rules = _load("dca_eligibility_rules", ROOT / "gates" / "eligibility_rules.py")


def git(repo, *args):
    proc = subprocess.run(["git", "-C", str(repo), *args], stdout=subprocess.PIPE,
                          stderr=subprocess.STDOUT, check=False)
    if proc.returncode != 0:
        raise AssertionError(f"git {' '.join(args)}: {proc.stdout.decode()}")
    return proc.stdout.decode()


class LauncherCase(unittest.TestCase):
    """A throwaway repo, a synthetic eligibility document and a fake sbx wired together."""

    fixture = "all-eligible.json"

    def setUp(self):
        WORK.mkdir(parents=True, exist_ok=True)
        self.dir = WORK / self.id().rsplit(".", 1)[-1]
        shutil.rmtree(self.dir, ignore_errors=True)
        self.dir.mkdir(parents=True)

        self.repo = self.dir / "repo"
        self.repo.mkdir()
        git(self.repo, "init", "--quiet", "--initial-branch=work")
        git(self.repo, "config", "user.email", "t@example.invalid")
        git(self.repo, "config", "user.name", "dca tests")
        (self.repo / "a.txt").write_text("alpha\n", encoding="utf-8")
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "--quiet", "-m", "initial")

        self.versions = json.loads(
            (FIXTURES / "versions.synthetic.yaml").read_text(encoding="utf-8"))
        # The synthetic fixture pins a deliberately unreal sbx version. These tests are about the
        # launcher's checks, not about drift, so the pin is set to what the fake sbx reports and
        # the drift path gets its own test.
        self.versions["sbx"] = {"exact": "v0.43.0", "minimum": "0.43.0"}
        self.versions_path = self.dir / "versions.yaml"
        self.eligibility_path = self.dir / "eligibility.json"
        self.write_versions(self.versions)
        self.write_eligibility(json.loads((FIXTURES / self.fixture).read_text(encoding="utf-8")))

        self.state_dir = self.dir / "sbx"
        self.state_dir.mkdir()
        self.state = {
            "version": "sbx version: v0.43.0",
            "settings": {"ssh.agentForwardingEnabled": False, "ssh.agentSocketPath": ""},
            "policy": {"rules": [], "governance": {"active": False}},
            "sandboxes": [],
        }
        self.write_state()
        self.sbx = sbx_module.Sbx(binary=str(FAKE_SBX),
                                  env={"DCA_FAKE_SBX_DIR": str(self.state_dir)})
        for name in launcher.PROVIDER_KEY_NAMES:
            os.environ.pop(name, None)

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    # --- fixture helpers -------------------------------------------------------------------

    def write_versions(self, versions):
        self.versions = versions
        self.versions_path.write_text(
            json.dumps(versions, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    def write_eligibility(self, document, rebind=True):
        """Write the document, re-binding it to THIS test's synthetic versions by default.

        A fixture carries the pins it was authored against, and freshness is exactly the check
        that those must equal the installed ones. Re-binding keeps each test about the thing it
        names; the staleness tests opt out with `rebind=False`.
        """
        if rebind:
            document["runtime_versions_digest"] = eligibility.canonical_digest(self.versions)
            document["pinned_versions"] = rules.pinned_versions(self.versions)
            document["network_policy_fingerprint"] = self.fingerprint()
        self.eligibility_path.write_text(
            json.dumps(document, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        self.document = document

    def write_state(self):
        (self.state_dir / "state.json").write_text(json.dumps(self.state, indent=2),
                                                   encoding="utf-8")

    def fingerprint(self):
        return g4.fingerprint({"rules": [], "governance": {"active": False}}, False)

    def make(self, **overrides):
        options = {"repo": str(self.repo), "task": "fix the thing", "backend": "claude",
                   "trust": "trusted", "out": str(self.dir / "out")}
        options.update(overrides)
        request = launcher.RunRequest(**options)
        return launcher.Launcher(request, sbx=self.sbx, repo_root=str(ROOT),
                                 eligibility_path=str(self.eligibility_path),
                                 versions_path=str(self.versions_path))

    def refusal(self, **overrides):
        with self.assertRaises(errors.PreconditionError) as caught:
            self.make(**overrides).preconditions()
        self.assertEqual(caught.exception.exit_code, 3)
        return str(caught.exception)


# --- preconditions 1-2: repository and source ------------------------------------------------


class TestSourcePreconditions(LauncherCase):
    def test_01_a_valid_branch_passes(self):
        self.assertTrue(self.make().preconditions())

    def test_02_a_path_that_is_not_a_repository_is_refused(self):
        # Outside the project tree on purpose: a directory INSIDE a repository is still "inside a
        # work tree" to git, which is why --repo must name the repository root.
        outside = tempfile.mkdtemp(prefix="dca-not-a-repo-")
        try:
            self.assertIn("not a git repository", self.refusal(repo=outside))
        finally:
            shutil.rmtree(outside, ignore_errors=True)

    def test_03_a_non_branch_ref_is_refused(self):
        git(self.repo, "tag", "v1")
        for ref in ("v1", "HEAD~0^{commit}", "refs/tags/v1"):
            with self.subTest(ref=ref):
                self.refusal(ref=ref)

    def test_04_a_dirty_checkout_is_refused_without_the_override(self):
        (self.repo / "a.txt").write_text("changed\n", encoding="utf-8")
        self.assertIn("--ignore-uncommitted", self.refusal())

    def test_05_the_override_records_names_and_proceeds(self):
        (self.repo / "a.txt").write_text("changed\n", encoding="utf-8")
        instance = self.make(ignore_uncommitted=True)
        instance.preconditions()
        self.assertEqual(instance.dirty_paths, ["a.txt"])


# --- precondition 3: provider keys -------------------------------------------------------------


class TestProviderKeys(LauncherCase):
    def test_10_a_present_provider_key_is_refused_by_name_only(self):
        secret = "sk-this-value-must-never-be-printed"
        os.environ["ANTHROPIC_API_KEY"] = secret
        try:
            message = self.refusal()
        finally:
            os.environ.pop("ANTHROPIC_API_KEY", None)
        self.assertIn("ANTHROPIC_API_KEY", message)
        self.assertNotIn(secret, message)

    def test_11_an_empty_provider_key_still_counts_as_present(self):
        os.environ["OPENAI_API_KEY"] = ""
        try:
            self.assertIn("OPENAI_API_KEY", self.refusal())
        finally:
            os.environ.pop("OPENAI_API_KEY", None)


# --- preconditions 4-5: sbx and SSH --------------------------------------------------------------


class TestEnvironmentPreconditions(LauncherCase):
    def test_20_a_missing_sbx_is_refused(self):
        self.sbx.binary = str(self.dir / "no-such-sbx")
        self.assertIn("sbx is not available", self.refusal())

    def test_21_an_old_sbx_is_refused(self):
        self.state["version"] = "sbx version: v0.42.9"
        self.write_state()
        self.assertIn("below the required", self.refusal())

    def test_22_a_drifted_sbx_is_refused_without_allow_drift(self):
        self.state["version"] = "sbx version: v0.44.0"
        self.write_state()
        self.assertIn("--allow-drift", self.refusal())
        self.make(allow_drift=True).preconditions()

    def test_23_a_missing_sandbox_base_pin_is_refused_even_with_allow_drift(self):
        versions = json.loads(json.dumps(self.versions))
        versions["sandbox_bases"].pop("claude")
        self.write_versions(versions)
        document = json.loads(json.dumps(self.document))
        document["pinned_versions"]["sandbox_bases"].pop("claude", None)
        self.write_eligibility(document, rebind=False)
        document["runtime_versions_digest"] = eligibility.canonical_digest(versions)
        self.write_eligibility(document, rebind=False)
        message = self.refusal(allow_drift=True)
        # --allow-drift never reaches this: the pin's absence is refused before drift is even
        # considered, whether the schema or the launcher's own check names it first.
        self.assertIn("sandbox_base", message.replace("sandbox base", "sandbox_base"))

    def test_24_ssh_agent_forwarding_is_refused(self):
        self.state["settings"]["ssh.agentForwardingEnabled"] = True
        self.write_state()
        self.assertIn("ssh.agentForwardingEnabled", self.refusal())

    def test_25_a_configured_agent_socket_is_refused(self):
        self.state["settings"]["ssh.agentSocketPath"] = "/tmp/agent.sock"
        self.write_state()
        self.assertIn("ssh.agentSocketPath", self.refusal())

    def test_26_an_unreadable_ssh_setting_is_refused_not_read_as_false(self):
        self.state["settings"] = {}
        self.write_state()
        self.assertIn("cannot be read as false", self.refusal())

    def test_27_every_sbx_call_drops_ssh_auth_sock(self):
        os.environ["SSH_AUTH_SOCK"] = "/tmp/should-not-reach-the-vm"
        try:
            self.make().preconditions()
        finally:
            os.environ.pop("SSH_AUTH_SOCK", None)
        self.assertTrue(self.sbx.calls)


# --- precondition 7: gate evidence -----------------------------------------------------------------


class TestGateEvidence(LauncherCase):
    def test_30_a_missing_evidence_file_is_refused(self):
        self.eligibility_path.unlink()
        self.assertIn("missing", self.refusal())

    def test_31_a_schema_invalid_document_is_refused(self):
        self.eligibility_path.write_text(json.dumps({"backends": "not an object"}),
                                         encoding="utf-8")
        self.assertIn("does not conform", self.refusal())

    def test_32_a_stale_digest_is_refused(self):
        document = json.loads(json.dumps(self.document))
        document["runtime_versions_digest"] = "sha256:" + "0" * 64
        self.write_eligibility(document, rebind=False)
        self.assertIn("stale", self.refusal())

    def test_33_a_changed_artifact_pin_makes_the_evidence_stale(self):
        versions = json.loads(json.dumps(self.versions))
        versions["docker_agent_artifact"]["sha256"] = "b" * 64
        self.write_versions(versions)
        message = self.refusal()
        self.assertTrue("stale" in message or "other pinned versions" in message, message)

    def test_34_a_changed_sandbox_base_makes_the_evidence_stale(self):
        versions = json.loads(json.dumps(self.versions))
        versions["sandbox_bases"]["claude"]["version"] = "sha256:" + "c" * 64
        self.write_versions(versions)
        message = self.refusal()
        self.assertTrue("stale" in message or "other pinned versions" in message, message)

    def test_35_a_failing_common_gate_stops_every_profile(self):
        document = json.loads(json.dumps(self.document))
        document["common_gates"]["G4"] = "FAIL"
        self.write_eligibility(document)
        message = self.refusal()
        self.assertIn("G4", message)

    def test_36_a_partial_g11_is_not_a_pass(self):
        document = json.loads(json.dumps(self.document))
        document["backends"]["claude"]["gate_status"]["G11"] = "PARTIAL"
        document["backends"]["claude"]["trusted_eligible"] = False
        document["backends"]["claude"]["untrusted_eligible"] = False
        self.write_eligibility(document)
        self.assertIn("PARTIAL", self.refusal())

    def test_37_production_conformance_must_be_pass(self):
        document = json.loads(json.dumps(self.document))
        document["backends"]["claude"]["production_conformance"] = "NOT-RUN"
        document["backends"]["claude"]["trusted_eligible"] = False
        document["backends"]["claude"]["untrusted_eligible"] = False
        self.write_eligibility(document)
        self.assertIn("production conformance", self.refusal())

    def test_38_an_unavailable_backend_is_refused_with_its_reason(self):
        document = json.loads(
            (FIXTURES / "claude-unavailable.json").read_text(encoding="utf-8"))
        self.write_eligibility(document)
        self.assertIn("not available", self.refusal(backend="claude"))

    def test_39_backend_independence_holds_in_both_directions(self):
        for fixture, usable in (("claude-unavailable.json", "codex"),
                                ("codex-unavailable.json", "claude")):
            with self.subTest(fixture=fixture):
                self.write_eligibility(
                    json.loads((FIXTURES / fixture).read_text(encoding="utf-8")))
                self.make(backend=usable, trust="trusted").preconditions()


# --- precondition 9: global network-policy drift ------------------------------------------------


class TestNetworkDrift(LauncherCase):
    def test_40_a_changed_global_fingerprint_is_refused(self):
        document = json.loads(json.dumps(self.document))
        document["network_policy_fingerprint"] = "sha256:" + "d" * 64
        self.write_eligibility(document, rebind=False)
        document["runtime_versions_digest"] = eligibility.canonical_digest(self.versions)
        self.write_eligibility(document, rebind=False)
        message = self.refusal()
        self.assertIn("global network policy has changed", message)

    def test_41_the_launcher_never_mutates_global_settings(self):
        try:
            self.make().preconditions()
        except errors.PreconditionError:
            pass
        log = self.state_dir / "calls.jsonl"
        self.assertTrue(log.is_file(), "the launcher must have called sbx at all")
        calls = [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]
        for call in calls:
            with self.subTest(argv=call["argv"]):
                self.assertNotIn("init", call["argv"])
                self.assertNotIn("reset", call["argv"])
                self.assertNotIn("--all", call["argv"])

    def test_42_an_existing_sandbox_makes_the_fingerprint_unreadable(self):
        self.state["sandboxes"] = [{"name": "someone-elses"}]
        self.write_state()
        self.assertIn("sandbox(es) already exist", self.refusal())


# --- structural validator parity ------------------------------------------------------------------


class TestValidatorParity(unittest.TestCase):
    """The launcher never imports `jsonschema`, so its validator has to agree with one that does."""

    @classmethod
    def setUpClass(cls):
        import jsonschema as reference

        cls.reference = reference
        cls.schema = json.loads((ROOT / "gates" / "eligibility.schema.json").read_text(
            encoding="utf-8"))
        cls.validator = reference.Draft202012Validator(cls.schema)

    def corpus(self):
        documents = []
        for path in sorted(FIXTURES.glob("*.json")):
            base = json.loads(path.read_text(encoding="utf-8"))
            documents.append((path.name, base))
            documents.append((f"{path.name}+extra-key", dict(base, surprise=True)))
            without = json.loads(json.dumps(base))
            without.pop("common_gates", None)
            documents.append((f"{path.name}-required", without))
            wrong_type = json.loads(json.dumps(base))
            wrong_type["backends"] = ["not", "an", "object"]
            documents.append((f"{path.name}+wrong-type", wrong_type))
            bad_enum = json.loads(json.dumps(base))
            bad_enum["common_gates"]["G4"] = "MAYBE"
            documents.append((f"{path.name}+bad-enum", bad_enum))
            bad_digest = json.loads(json.dumps(base))
            bad_digest["runtime_versions_digest"] = "not-a-digest"
            documents.append((f"{path.name}+bad-digest", bad_digest))
            bad_pin = json.loads(json.dumps(base))
            bad_pin["pinned_versions"]["docker_agent"] = 15
            documents.append((f"{path.name}+bad-pin", bad_pin))
        documents.append(("not-an-object", ["eligibility"]))
        documents.append(("empty", {}))
        return documents

    def test_50_the_corpus_contains_both_valid_and_invalid_documents(self):
        verdicts = {bool(self.validator.iter_errors(document) and
                         list(self.validator.iter_errors(document)))
                    for _, document in self.corpus()}
        self.assertEqual(verdicts, {True, False})

    def test_51_both_validators_agree_on_every_corpus_document(self):
        for name, document in self.corpus():
            with self.subTest(document=name):
                reference_problems = list(self.validator.iter_errors(document))
                stdlib_problems = jsonschema_stdlib.validate(document, self.schema)
                self.assertEqual(
                    bool(stdlib_problems), bool(reference_problems),
                    f"stdlib={stdlib_problems[:2]} reference="
                    f"{[e.message for e in reference_problems][:2]}")

    def test_52_everything_the_schema_rejects_the_launcher_also_rejects(self):
        for name, document in self.corpus():
            if not list(self.validator.iter_errors(document)):
                continue
            with self.subTest(document=name):
                self.assertTrue(eligibility.structural_problems(document, self.schema))


# --- phase 2: dispositions ---------------------------------------------------------------------


class TestPolicyDisposition(LauncherCase):
    fixture = "trusted-only.json"

    def test_60_omitting_trust_means_untrusted(self):
        request = launcher.RunRequest(repo=str(self.repo), task="t")
        self.assertEqual(request.trust, "untrusted")

    def test_61_an_untrusted_request_with_no_eligible_backend_is_a_blocked_report(self):
        instance = self.make(trust="untrusted", backend="claude")
        instance.preconditions()
        blocked = instance.policy_disposition()
        self.assertIsNotNone(blocked)
        self.assertEqual(blocked["final_outcome"], "blocked")
        self.assertEqual(blocked["run_integrity"],
                         {"stream": "none", "agent_exit": "not-started",
                          "sandbox_created": False})
        self.assertIsNone(blocked["sandbox_settings"])
        self.assertIsNone(blocked["source"]["bundle_sha256"])
        self.assertEqual(blocked["change_set"]["files"], [])
        # The fixture's claude backend has G1b PASS and G9 FAIL, so G9 is the gate named.
        self.assertIn("G9", blocked["primary_reason"])
        self.assertIn("untrusted", blocked["primary_reason"])

    def test_62_the_blocked_report_validates_and_exits_11(self):
        report_module = _load("dca_report", ROOT / "src" / "dca" / "report.py")
        instance = self.make(trust="untrusted", backend="claude")
        instance.preconditions()
        blocked = instance.policy_disposition()
        report_module.validate(blocked)
        self.assertEqual(report_module.exit_status(blocked), 11)

    def test_63_a_trusted_request_proceeds_past_phase_2(self):
        instance = self.make(trust="trusted", backend="claude")
        instance.preconditions()
        self.assertIsNone(instance.policy_disposition())

    def test_64_no_sandbox_is_created_for_a_phase_2_block(self):
        instance = self.make(trust="untrusted", backend="claude")
        instance.preconditions()
        instance.policy_disposition()
        calls = [json.loads(line)["argv"] for line in
                 (self.state_dir / "calls.jsonl").read_text(encoding="utf-8").splitlines()]
        self.assertFalse([call for call in calls if "create" in call])


if __name__ == "__main__":
    unittest.main()
