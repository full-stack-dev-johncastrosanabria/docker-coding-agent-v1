"""Launcher Phase 3A: provisioning and source delivery (tasks.md T066).

Against the fake `sbx`, so every assertion is about the command the launcher ACTUALLY built -
the sandbox base, `--skills off`, the per-sandbox network rules, what was copied in - rather than
about what a helper claims it would build.

The order matters as much as the contents, and the tests say so: the source bundle exists before
any sandbox does (so a source failure never strands a VM), and the network policy is applied before
anything is copied in (so the sandbox is never briefly reachable on a wider policy than the run is
entitled to).

THE CODEX CREDENTIAL CASE IS THE SHARPEST ONE HERE. The trusted token-file mechanism copies exactly
one file into a minimal directory; the whole host config dir is never copied, an untrusted request
can never reach it, and neither its contents nor any hash of it may appear in any artifact the run
produces.
"""

import importlib.util
import json
import os
import shutil
import subprocess
import sys
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


class ProvisioningCase(unittest.TestCase):
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
        self.commit = git(self.repo, "rev-parse", "HEAD").strip()

        self.versions = json.loads(
            (FIXTURES / "versions.synthetic.yaml").read_text(encoding="utf-8"))
        self.versions["sbx"] = {"exact": "v0.43.0", "minimum": "0.43.0"}
        self.versions_path = self.dir / "versions.yaml"
        self.versions_path.write_text(json.dumps(self.versions, indent=2, sort_keys=True),
                                      encoding="utf-8")

        self.document = json.loads((FIXTURES / self.fixture).read_text(encoding="utf-8"))
        self.eligibility_path = self.dir / "eligibility.json"
        self.write_eligibility()

        self.state_dir = self.dir / "sbx"
        self.state_dir.mkdir()
        self.state = {
            "version": "sbx version: v0.43.0",
            "settings": {"ssh.agentForwardingEnabled": False, "ssh.agentSocketPath": ""},
            "policy": {"rules": [], "governance": {"active": False}},
            "sandboxes": [],
            "create_output": "created from synthetic.invalid/claude-base",
        }
        self.write_state()
        self.sbx = sbx_module.Sbx(binary=str(FAKE_SBX),
                                  env={"DCA_FAKE_SBX_DIR": str(self.state_dir)})
        for name in launcher.PROVIDER_KEY_NAMES:
            os.environ.pop(name, None)

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def write_eligibility(self):
        self.document["runtime_versions_digest"] = eligibility.canonical_digest(self.versions)
        self.document["pinned_versions"] = rules.pinned_versions(self.versions)
        self.document["network_policy_fingerprint"] = g4.fingerprint(
            {"rules": [], "governance": {"active": False}}, False)
        self.eligibility_path.write_text(json.dumps(self.document, indent=2, sort_keys=True),
                                         encoding="utf-8")

    def write_state(self):
        (self.state_dir / "state.json").write_text(json.dumps(self.state, indent=2),
                                                   encoding="utf-8")

    def stub_kit(self, path, backend):
        os.makedirs(path, exist_ok=True)
        with open(os.path.join(path, "spec.yaml"), "w", encoding="utf-8") as handle:
            handle.write(f"# stub kit for {backend}\n")
        return path

    def make(self, **overrides):
        options = {"repo": str(self.repo), "task": "fix the thing", "backend": "claude",
                   "trust": "trusted", "out": str(self.dir / "out")}
        options.update(overrides)
        request = launcher.RunRequest(**options)
        return launcher.Launcher(request, sbx=self.sbx, repo_root=str(ROOT),
                                 eligibility_path=str(self.eligibility_path),
                                 versions_path=str(self.versions_path),
                                 kit_builder=self.stub_kit)

    def provisioned(self, **overrides):
        backend = overrides.get("backend", "claude")
        # The fake echoes what a real `sbx create` echoes: the base it resolved. Setting it from
        # the pin is what lets the launcher's "never substituted" check be exercised honestly.
        self.state["create_output"] = (
            f"created from {self.versions['sandbox_bases'][backend]['base']}")
        self.write_state()
        instance = self.make(**overrides)
        instance.preconditions()
        instance.provision()
        return instance

    def calls(self):
        log = self.state_dir / "calls.jsonl"
        if not log.is_file():
            return []
        return [json.loads(line)["argv"][1:]
                for line in log.read_text(encoding="utf-8").splitlines()]

    def first(self, subcommand):
        for argv in self.calls():
            if argv and argv[0] == subcommand:
                return argv
        return None


class TestHappyPath(ProvisioningCase):
    def test_01_the_source_bundle_is_created_from_the_branch_ref(self):
        instance = self.provisioned()
        self.assertEqual(instance.source_ref, "refs/heads/work")
        self.assertEqual(instance.source_commit, self.commit)
        self.assertRegex(instance.bundle_sha256, r"^[0-9a-f]{64}$")

    def test_02_the_sandbox_is_mountless_with_shared_skills_off_and_the_kit(self):
        self.provisioned()
        create = self.first("create")
        self.assertIsNotNone(create)
        self.assertIn("--skills", create)
        self.assertEqual(create[create.index("--skills") + 1], "off")
        self.assertIn("--kit", create)
        self.assertNotIn("--mount", create)
        self.assertNotIn("-v", create)

    def test_03_the_sandbox_is_named_for_the_run(self):
        instance = self.provisioned()
        create = self.first("create")
        self.assertEqual(create[create.index("--name") + 1], f"dca-{instance.request.run_id}")

    def test_04_the_template_matches_the_backend(self):
        for backend, template in (("claude", "claude"), ("codex", "docker-agent")):
            with self.subTest(backend=backend):
                self.setUp()
                self.provisioned(backend=backend)
                self.assertEqual(self.first("create")[1], template)

    def test_05_the_bundle_exists_before_any_sandbox_is_created(self):
        instance = self.make()
        instance.preconditions()
        order = []
        original = self.sbx.create

        def watched(template, name, kit):
            order.append("create")
            return original(template, name, kit)

        self.sbx.create = watched
        original_bundle = launcher.source_module.create_source_bundle

        def watched_bundle(*args, **kwargs):
            order.append("bundle")
            return original_bundle(*args, **kwargs)

        launcher.source_module.create_source_bundle = watched_bundle
        try:
            instance.provision()
        finally:
            launcher.source_module.create_source_bundle = original_bundle
        self.assertEqual(order[:2], ["bundle", "create"])

    def test_06_the_network_policy_is_applied_before_anything_is_copied_in(self):
        self.provisioned()
        sequence = [argv[0] for argv in self.calls()]
        self.assertIn("policy", sequence)
        self.assertIn("cp", sequence)
        self.assertLess(sequence.index("policy"), sequence.index("cp"))

    def test_07_the_applied_allow_set_is_the_profile_cell_from_network_yaml(self):
        instance = self.provisioned()
        allow, deny = instance.network_cell()
        applied = json.loads((self.state_dir / "state.json").read_text(
            encoding="utf-8")).get("applied_rules", [])
        allowed = [entry for entry in applied if entry[:2] == ["allow", "network"]]
        self.assertTrue(allowed)
        self.assertEqual(sorted(allowed[0][-1].split(",")), sorted(allow))
        if deny:
            denied = [entry for entry in applied if entry[:2] == ["deny", "network"]]
            self.assertEqual(sorted(denied[0][-1].split(",")), sorted(deny))

    def test_08_the_untrusted_profile_is_narrower_than_the_trusted_one(self):
        trusted_allow, _ = self.make(trust="trusted").network_cell()
        untrusted_allow, _ = self.make(trust="untrusted").network_cell()
        self.assertLess(len(untrusted_allow), len(trusted_allow))
        self.assertTrue(set(untrusted_allow).issubset(set(trusted_allow)))

    def test_09_the_run_record_and_task_are_delivered(self):
        self.provisioned()
        copied = [argv for argv in self.calls() if argv and argv[0] == "cp"]
        self.assertTrue(any("dca-src.bundle" in argv[-1] for argv in copied))
        self.assertTrue(any("/tmp/dca-run" in argv[-1] for argv in copied))

    def test_10_sandbox_settings_record_what_was_actually_applied(self):
        instance = self.provisioned()
        self.assertEqual(instance.sandbox_settings["mountless"], True)
        self.assertEqual(instance.sandbox_settings["shared_skills"], "off")
        self.assertEqual(instance.sandbox_settings["ssh_agent_forwarding"], False)
        self.assertRegex(instance.sandbox_settings["network_policy_digest"],
                         r"^sha256:[0-9a-f]{64}$")


class TestInfrastructureAborts(ProvisioningCase):
    def fail_at(self, key, status=1):
        self.state.setdefault("fail", {})[key] = status
        self.write_state()

    def test_20_a_source_bundle_failure_creates_no_sandbox(self):
        instance = self.make()
        instance.preconditions()
        instance.source_commit = "0" * 40
        with self.assertRaises(errors.InfraAbort) as caught:
            instance.provision()
        self.assertEqual(caught.exception.exit_code, 4)
        self.assertIsNone(self.first("create"))

    def test_21_each_provisioning_step_failing_is_an_infrastructure_abort(self):
        for key in ("create", "cp", "exec"):
            with self.subTest(step=key):
                self.setUp()
                self.fail_at(key)
                instance = self.make()
                instance.preconditions()
                with self.assertRaises(errors.InfraAbort) as caught:
                    instance.provision()
                self.assertEqual(caught.exception.exit_code, 4)

    def test_22_a_policy_failure_is_an_infrastructure_abort(self):
        self.fail_at("policy allow")
        instance = self.make()
        instance.preconditions()
        with self.assertRaises(errors.InfraAbort):
            instance.provision()

    def test_23_cleanup_is_attempted_after_a_provisioning_failure(self):
        self.fail_at("exec")
        instance = self.make()
        instance.preconditions()
        with self.assertRaises(errors.InfraAbort):
            instance.provision()
        instance.cleanup()
        removed = json.loads((self.state_dir / "state.json").read_text(
            encoding="utf-8")).get("removed", [])
        self.assertTrue(removed)


class TestCodexCredential(ProvisioningCase):
    """The token-file-trusted-only mechanism: exactly one file, trusted runs only."""

    def setUp(self):
        super().setUp()
        self.document["backends"]["codex"]["credential_mechanism"] = "token-file-trusted-only"
        self.write_eligibility()
        self.home_config = self.dir / "cagent"
        self.home_config.mkdir()
        self.secret = "TOKEN-VALUE-THAT-MUST-NOT-LEAK"
        (self.home_config / "chatgpt-auth.json").write_text(
            json.dumps({"token": self.secret}), encoding="utf-8")
        (self.home_config / "user-uuid").write_text("uuid\n", encoding="utf-8")
        (self.home_config / "config.yaml").write_text("telemetry_enabled: false\n",
                                                      encoding="utf-8")
        self._original_home = launcher.HOST_CONFIG_DIR
        launcher.HOST_CONFIG_DIR = str(self.home_config)

    def tearDown(self):
        launcher.HOST_CONFIG_DIR = self._original_home
        super().tearDown()

    def copied_credential_dirs(self):
        return [argv for argv in self.calls()
                if argv and argv[0] == "cp" and launcher.VM_CONFIG_DIR in argv[-1]]

    def test_30_a_trusted_codex_run_copies_exactly_one_credential_file(self):
        instance = self.provisioned(backend="codex", trust="trusted")
        copied = self.copied_credential_dirs()
        self.assertEqual(len(copied), 1)
        staged = Path(copied[0][1])
        self.assertIn(instance.request.run_id, str(staged))

    def test_31_the_whole_host_config_directory_is_never_copied(self):
        self.provisioned(backend="codex", trust="trusted")
        for argv in self.calls():
            with self.subTest(argv=argv):
                self.assertNotIn(str(self.home_config), " ".join(argv))

    def test_32_a_proxy_managed_mechanism_copies_nothing(self):
        self.document["backends"]["codex"]["credential_mechanism"] = "proxy-managed"
        self.write_eligibility()
        self.provisioned(backend="codex", trust="trusted")
        self.assertEqual(self.copied_credential_dirs(), [])

    def test_33_a_claude_run_never_stages_a_codex_credential(self):
        self.provisioned(backend="claude", trust="trusted")
        self.assertEqual(self.copied_credential_dirs(), [])

    def test_34_no_artifact_contains_the_token_value_or_a_hash_of_it(self):
        import hashlib

        instance = self.provisioned(backend="codex", trust="trusted")
        digest = hashlib.sha256(self.secret.encode()).hexdigest()
        haystacks = [json.dumps(self.calls())]
        for path in Path(instance.request.out).rglob("*"):
            if path.is_file():
                haystacks.append(path.read_text(encoding="utf-8", errors="replace"))
        for text in haystacks:
            self.assertNotIn(self.secret, text)
            self.assertNotIn(digest, text)

    def test_35_the_staged_material_is_removed_with_the_sandbox(self):
        instance = self.provisioned(backend="codex", trust="trusted")
        workdir = instance.workdir
        instance.cleanup()
        self.assertFalse(os.path.exists(workdir))


if __name__ == "__main__":
    unittest.main()
