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
import re
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
        instance = self.make(**overrides)
        instance.preconditions()
        instance.provision()
        return instance

    def resolved_image(self, backend="claude", reference=None):
        """A creation record shaped like sbx's own: the `image` line naming what it resolved."""
        if reference is None:
            pinned = self.versions["sandbox_bases"][backend]
            reference = f"{pinned['base']}@{pinned['version']}"
        return ("\u2500\u2500 RESOLVE SETUP\n"
                f"     image      {reference}\n"
                "   \u2713 configuration resolved\n")

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

        def watched(template, name, kit, base):
            order.append("create")
            return original(template, name, kit, base)

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


class TestCleanupAccounting(ProvisioningCase):
    """`cleanup` must only try to remove a sandbox whose creation was actually attempted.

    The bug: `self.sandbox` was assigned before the pinned-base guard, a purely local check on
    runtime/versions.yaml that never talks to sbx. A config-only failure therefore reached
    `cleanup()`, which runs `sbx rm --force <name>` for any non-None `self.sandbox` and records
    `removal_failed` on a non-zero exit - so a sandbox that was never created could be reported as a
    CLEANUP FAILURE. Cleanup failures are a headline acceptance number, so a false one is not
    cosmetic: it makes the run's own report untrue.
    """

    def test_30_a_failure_before_creation_attempts_no_removal(self):
        instance = self.make(backend="claude")
        instance.preconditions()
        del instance.versions["sandbox_bases"]["claude"]["version"]
        with self.assertRaises(errors.InfraAbort):
            instance.provision()
        instance.cleanup()
        self.assertIsNone(self.first("rm"), "nothing was created, so nothing may be removed")
        self.assertIsNone(instance.removal_failed, "a cleanup that never ran cannot have failed")
        state = json.loads((self.state_dir / "state.json").read_text(encoding="utf-8"))
        self.assertFalse(state.get("removed"), "sbx was never asked to remove anything")

    def test_31_a_created_sandbox_is_still_removed(self):
        instance = self.provisioned()
        instance.cleanup()
        self.assertIsNotNone(self.first("rm"))
        self.assertIsNone(instance.removal_failed)
        state = json.loads((self.state_dir / "state.json").read_text(encoding="utf-8"))
        self.assertTrue(state.get("removed"))

    def test_32_a_sandbox_left_by_a_failed_creation_is_still_removed(self):
        """A create that fails partway can leave a sandbox, so the name is claimed before the call."""
        self.state["create_output"] = "Created sandbox but resolved nothing\n"
        self.write_state()
        instance = self.make(backend="claude")
        instance.preconditions()
        with self.assertRaises(errors.InfraAbort):
            instance.provision()
        instance.cleanup()
        self.assertIsNotNone(self.first("rm"), "an unprovable base still leaves a sandbox to remove")

    def test_33_a_real_removal_failure_is_still_recorded(self):
        instance = self.provisioned()
        self.state["fail"] = {"rm": 1}
        self.write_state()
        instance.cleanup()
        self.assertEqual(instance.removal_failed, f"dca-{instance.request.run_id}")


class TestManagedFingerprintCollection(ProvisioningCase):
    """The launcher collects the managed hook record itself, and always hands it to the report.

    FR-022's evidence must reach the host without the agent's help. Two things are pinned here: the
    record is copied out of the VM while the sandbox still exists, and `review_fingerprints` is
    never left as `None` once there is an agent report to judge - because `None` means "the caller
    supplied no managed evidence", which falls back to reading the agent's own claim. In production
    that fallback must be unreachable.
    """

    RECORD = ('{"ts": "2026-09-26T00:00:00Z", "event": "SubagentStart", "agent": "reviewer", '
              '"fingerprint": "sha256:%s", "error": null}\n'
              '{"ts": "2026-09-26T00:00:01Z", "event": "SubagentStop", "agent": "reviewer", '
              '"fingerprint": "sha256:%s", "error": null}\n') % ("a" * 64, "a" * 64)

    def collected(self, record=None, agent_report='{"outcome": "succeeded"}'):
        instance = self.provisioned()
        payload = {f"{launcher.SCRATCH_DIR}/report.agent.json": agent_report}
        if record is not None:
            payload[f"{launcher.STATE_DIR}/fingerprints.jsonl"] = record
        self.state["copy_out"] = payload
        self.write_state()
        return instance, instance.collect_agent_evidence()

    def test_35_the_managed_record_is_copied_out_of_the_running_sandbox(self):
        instance, _ = self.collected(self.RECORD)
        copied = os.path.join(instance.request.out, "fingerprints.jsonl")
        self.assertTrue(os.path.isfile(copied), "the record reaches the host as a file")
        # ...and while the sandbox still exists: the copy precedes any removal.
        commands = [argv[0] for argv in self.calls()]
        self.assertIn("cp", commands)
        self.assertNotIn("rm", commands[:commands.index("cp")])

    def test_36_the_collected_record_is_parsed_into_the_review_evidence(self):
        instance, _ = self.collected(self.RECORD)
        self.assertEqual(len(instance.review_fingerprints), 2)
        identity = launcher.fingerprint_module.review_identity(instance.review_fingerprints)
        self.assertTrue(identity["proven"])

    def test_37_a_missing_record_is_an_empty_list_never_None(self):
        """Absent evidence must read as "gathered nothing", which is unproven - not as no opinion."""
        instance, report = self.collected(None)
        self.assertIsNotNone(report, "the agent report was still collected")
        self.assertEqual(instance.review_fingerprints, [])
        self.assertFalse(
            launcher.fingerprint_module.review_identity(instance.review_fingerprints)["proven"])

    def test_38_an_unparsable_record_never_aborts_the_run(self):
        instance, _ = self.collected("not json\n{\n")
        self.assertEqual(instance.review_fingerprints, [])


class TestImmutableBase(ProvisioningCase):
    """The sandbox is created FROM the pinned digest, and that is what is then proven.

    The defect this replaces: the launcher created from the agent default - a mutable tag - and
    audited the digest afterwards against `sbx template ls`, which reports what that same mutable
    tag maps to. When upstream moved the tag, the pinned image was still present and bootable, but
    no run could be provisioned: the audit compared the pin against a newer digest and aborted.
    Selecting the image by digest at creation time removes the tag from the decision entirely, so
    an upstream move cannot choose the image OR invalidate a correct run.
    """

    CLAUDE = ("docker/sandbox-templates:claude-code-docker", "sha256:" + "9" * 12 + "a" * 52)
    CODEX = ("docker/sandbox-templates:docker-agent-docker", "sha256:" + "6" * 12 + "b" * 52)
    #: Where the mutable tag points after upstream moves it. Never a pin, never in product logic.
    MOVED = "sha256:" + "5" * 12 + "c" * 52

    def setUp(self):
        super().setUp()
        # Real-shaped pins: both bases share one repository and differ only by tag and digest,
        # which is exactly the case a repository-only comparison cannot tell apart.
        self.versions["sandbox_bases"] = {
            "claude": {"base": self.CLAUDE[0], "version": self.CLAUDE[1]},
            "codex": {"base": self.CODEX[0], "version": self.CODEX[1]},
        }
        self.versions_path.write_text(json.dumps(self.versions, indent=2, sort_keys=True),
                                      encoding="utf-8")
        self.write_eligibility()
        self.write_state()

    @staticmethod
    def image(reference, digest, repository_prefix="docker.io/"):
        repository, _, tag = reference.rpartition(":")
        return {"id": digest[len("sha256:"):][:12], "repository": repository_prefix + repository,
                "tag": tag, "flavor": tag}

    def pinned(self, backend):
        reference, digest = self.CLAUDE if backend == "claude" else self.CODEX
        return reference, digest, f"{reference}@{digest}"

    def provision_with(self, record, backend="claude"):
        self.state["create_output"] = record
        self.write_state()
        instance = self.make(backend=backend)
        instance.preconditions()
        instance.provision()
        return instance

    def assert_aborts_before_policy(self, record, backend="claude", *fragments):
        self.state["create_output"] = record
        self.write_state()
        instance = self.make(backend=backend)
        instance.preconditions()
        with self.assertRaises(errors.InfraAbort) as caught:
            instance.provision()
        self.assertEqual(caught.exception.exit_code, 4)
        message = str(caught.exception)
        for fragment in fragments:
            self.assertIn(fragment, message)
        commands = [argv[:2] for argv in self.calls()]
        self.assertNotIn(["policy", "allow"], commands, "no network policy after a bad base")
        self.assertNotIn("cp", [argv[0] for argv in self.calls()], "nothing copied in")
        instance.cleanup()
        removed = json.loads((self.state_dir / "state.json").read_text(
            encoding="utf-8")).get("removed", [])
        self.assertTrue(removed, "the sandbox created from an unproven base is removed")
        return message

    # --- creation uses the immutable digest ---------------------------------------------------

    def test_40_create_requests_the_digest_qualified_reference(self):
        for backend in ("claude", "codex"):
            with self.subTest(backend=backend):
                self.setUp()
                _, _, reference = self.pinned(backend)
                self.provision_with(self.resolved_image(reference=reference), backend)
                create = self.first("create")
                self.assertIn("--template", create)
                self.assertEqual(create[create.index("--template") + 1], reference)

    def test_41_each_backend_requests_its_own_pinned_base(self):
        requested = {}
        for backend in ("claude", "codex"):
            self.setUp()
            _, _, reference = self.pinned(backend)
            self.provision_with(self.resolved_image(reference=reference), backend)
            create = self.first("create")
            requested[backend] = create[create.index("--template") + 1]
        self.assertEqual(requested, {"claude": self.pinned("claude")[2],
                                     "codex": self.pinned("codex")[2]})
        self.assertNotEqual(requested["claude"], requested["codex"])

    def test_42_versions_yaml_is_the_only_source_of_the_requested_base(self):
        # Change the pin and nothing else; the request must follow it, with no cached second copy.
        moved = f"{self.CLAUDE[0]}@{self.MOVED}"
        self.versions["sandbox_bases"]["claude"]["version"] = self.MOVED
        self.versions_path.write_text(json.dumps(self.versions, indent=2, sort_keys=True),
                                      encoding="utf-8")
        self.write_eligibility()
        self.provision_with(self.resolved_image(reference=moved))
        create = self.first("create")
        self.assertEqual(create[create.index("--template") + 1], moved)

    def test_43_no_digest_or_image_name_is_written_into_the_product(self):
        # Every base identity must come from runtime/versions.yaml. A literal digest or image name
        # in the product would be a second pin source, and the one upstream moved would be baked in.
        literal = re.compile(r"[0-9a-f]{40,}")
        for path in (ROOT / "src" / "dca" / "launcher.py", ROOT / "src" / "dca" / "sbx.py"):
            body = path.read_text(encoding="utf-8")
            with self.subTest(path=path.name):
                self.assertNotIn("sandbox-templates", body)
                self.assertIsNone(literal.search(body),
                                  "no image digest may be hardcoded in the product")

    # --- the tag may move; the pin still boots ------------------------------------------------

    def test_44_a_moved_mutable_tag_does_not_affect_provisioning(self):
        """The regression: the store maps the tag to a NEWER digest and the run still succeeds."""
        reference, digest, pinned = self.pinned("claude")
        self.state["templates"] = {"images": [self.image(reference, self.MOVED),
                                              self.image(*self.CODEX)]}
        instance = self.provision_with(self.resolved_image(reference=pinned))
        self.assertTrue(instance.sandbox_settings["mountless"])
        self.assertEqual(instance.sandbox_settings["shared_skills"], "off")
        # And the pin itself was not touched to get there.
        self.assertEqual(
            json.loads(self.versions_path.read_text(encoding="utf-8"))
            ["sandbox_bases"]["claude"]["version"], digest)

    def test_45_the_template_store_is_never_consulted(self):
        """It reports the mutable tag's mapping, so it can neither prove nor disprove the pin."""
        _, _, pinned = self.pinned("claude")
        self.state["fail"] = {"template": 1}      # any read of it would abort the run
        self.provision_with(self.resolved_image(reference=pinned))
        self.assertNotIn("template", [argv[0] for argv in self.calls()])

    # --- fail closed --------------------------------------------------------------------------

    def test_46_a_record_naming_only_the_mutable_tag_is_refused(self):
        reference, _, pinned = self.pinned("claude")
        message = self.assert_aborts_before_policy(
            self.resolved_image(reference=reference), "claude", pinned)
        self.assertIn(reference, message)

    def test_47_a_record_naming_a_different_digest_is_refused(self):
        reference, _, pinned = self.pinned("claude")
        moved = f"{reference}@{self.MOVED}"
        message = self.assert_aborts_before_policy(self.resolved_image(reference=moved),
                                                   "claude", pinned)
        self.assertIn(self.MOVED, message)

    def test_47a_the_reference_must_be_on_a_resolved_image_line(self):
        """The pinned string appearing ANYWHERE in the output is not proof it was resolved.

        It legitimately appears in the line that pulls it, and would appear in an echoed invocation
        or a "did you mean" hint. A create that resolved a different image can therefore print it
        and still exit 0, so only the resolved-image line may decide.
        """
        reference, _, pinned = self.pinned("claude")
        moved = f"{reference}@{self.MOVED}"
        for record in (
            # the pin named only in a pull/progress line, while a different image was resolved
            f"   \u2192 pull {pinned}\n     image      {moved}\n",
            # the pin named in a hint, with nothing resolved at all
            f"   ! {pinned} not found, did you mean it?\n   \u2713 Created sandbox dca-x\n",
            # the pin only as part of a longer reference on the image line
            f"     image      {pinned}-patched\n",
        ):
            with self.subTest(record=record.strip()[:48]):
                self.setUp()
                self.assert_aborts_before_policy(record, "claude", pinned)

    def test_48_the_other_backends_pinned_base_is_refused(self):
        self.assert_aborts_before_policy(
            self.resolved_image(reference=self.pinned("codex")[2]), "claude",
            self.pinned("claude")[2])

    def test_49_a_record_with_no_image_line_is_refused(self):
        message = self.assert_aborts_before_policy("Created sandbox dca-x\n", "claude")
        self.assertIn("(no image line)", message)

    def test_50_an_empty_record_is_refused(self):
        self.assert_aborts_before_policy("", "claude", "cannot be shown")

    def test_51_an_incomplete_pin_is_refused_before_anything_is_created(self):
        """Both halves of the pin are required, at the precondition AND at the creation guard."""
        for missing in ("base", "version"):
            with self.subTest(missing=missing, stage="preconditions"):
                self.setUp()
                del self.versions["sandbox_bases"]["claude"][missing]
                self.versions_path.write_text(json.dumps(self.versions, indent=2, sort_keys=True),
                                              encoding="utf-8")
                self.write_eligibility()
                with self.assertRaises(errors.PreconditionError):
                    self.make(backend="claude").preconditions()
                self.assertIsNone(self.first("create"), "nothing created without a complete pin")
            with self.subTest(missing=missing, stage="provision guard"):
                # Defence in depth: even if the pin were lost after the preconditions passed,
                # provisioning refuses rather than letting sbx fall back to the agent default.
                self.setUp()
                instance = self.make(backend="claude")
                instance.preconditions()
                del instance.versions["sandbox_bases"]["claude"][missing]
                with self.assertRaises(errors.InfraAbort) as caught:
                    instance.provision()
                self.assertIn("pins no complete sandbox base", str(caught.exception))
                self.assertIsNone(self.first("create"), "nothing created without a complete pin")

    def test_52_the_adapter_refuses_to_create_without_a_pinned_base(self):
        """Belt and braces: even called directly, `create` never falls back to the agent default."""
        for base in (None, ""):
            with self.subTest(base=base):
                with self.assertRaises(ValueError) as caught:
                    self.sbx.create("claude", "dca-x", str(self.dir), base)
                self.assertIn("mutable tag", str(caught.exception))

    def test_53_a_missing_pin_still_fails_closed_in_the_verification_itself(self):
        del self.versions["sandbox_bases"]["claude"]
        instance = self.make(backend="claude")
        instance.versions = self.versions
        with self.assertRaises(errors.InfraAbort) as caught:
            instance._check_resolved_base(self.resolved_image(reference=self.pinned("claude")[2]))
        self.assertIn("no pinned sandbox base", str(caught.exception))

    # --- everything else unchanged -------------------------------------------------------------

    def test_54_network_mountless_skills_and_ssh_behaviour_are_unchanged(self):
        _, _, pinned = self.pinned("claude")
        instance = self.provision_with(self.resolved_image(reference=pinned))
        self.assertTrue(instance.sandbox_settings["mountless"])
        self.assertEqual(instance.sandbox_settings["shared_skills"], "off")
        self.assertFalse(instance.sandbox_settings["ssh_agent_forwarding"])
        create = self.first("create")
        self.assertEqual(create[create.index("--skills") + 1], "off")
        self.assertIn("--kit", create)
        self.assertNotIn("--mount", create)
        self.assertNotIn("-v", create)
        commands = [argv[:2] for argv in self.calls()]
        self.assertIn(["policy", "allow"], commands)
        self.assertLess(commands.index(["policy", "allow"]),
                        [argv[0] for argv in self.calls()].index("cp"))


if __name__ == "__main__":
    unittest.main()
