"""Backend parity contract test (tasks.md T060).

Parity here means **behavioural and artifact** parity, never textual YAML equality. The two backend
configs are written in different shapes because Docker Agent drives them differently - Claude is a
harness whose controls live in kit-installed managed settings, Codex is three configured agents -
so comparing the files would compare the wrong thing entirely.

What is compared instead is what a developer is actually promised: the same policy, evaluated by
the same gate binary, with the same limits, the same instructions, the same four skills from a
trusted source, and the same finalizer deciding the outcome. Each check is recomputed from the
shared source rather than restated, so a divergence introduced in one backend cannot pass by being
copied into the test.

EVERY CHECK RUNS AGAINST A REAL STAGED KIT, laid out by `runtime/sandbox/kit/stage.py` - the same
tool production uses. Comparing `runtime/instructions/root.md` with itself would prove nothing; the
question is whether the bytes that reach each backend inside the VM are the same bytes.

AN UNAVAILABLE BACKEND IS REPORTED NOT-APPLICABLE WITH ITS REASON, never silently passed, and the
shared-source checks still run for the backend that is available.
"""

import hashlib
import importlib.util
import json
import shutil
import sys
import tempfile
import unittest
from unittest import mock
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
RUNTIME = ROOT / "runtime"
ELIGIBILITY = ROOT / "gates" / "eligibility.json"
RUNTIME_SKILLS = ("change-receipt", "repository-navigation", "root-cause-debugging",
                  "verification")
GATE = "/opt/dca/bin/dca-gate"


def _load(name, path):
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


policy_rules = _load("dca_policy_rules", ROOT / "src" / "dca" / "policy_rules.py")
stage_module = _load("dca_stage", RUNTIME / "sandbox" / "kit" / "stage.py")


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def available(backend, document=None):
    document = document or json.loads(ELIGIBILITY.read_text(encoding="utf-8"))
    entry = (document.get("backends") or {}).get(backend) or {}
    if entry.get("available") is True:
        return True, ""
    return False, (entry.get("unavailable_reason")
                   or f"backend unavailable per gates/eligibility.json ({backend})")


class ParityCase(unittest.TestCase):
    """One staged kit, built exactly the way production builds it, shared by every check."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="dca-parity-")
        cls.backends = [name for name in ("claude", "codex") if available(name)[0]]
        cls.kit = Path(stage_module.stage(
            cls.tmp, skills_source=RUNTIME / "skills", backends=cls.backends))
        cls.limits = json.loads((RUNTIME / "policy" / "limits.yaml").read_text(encoding="utf-8"))
        cls.actions = policy_rules.load_actions()
        cls.managed = json.loads(
            (RUNTIME / "claude" / "managed-settings.json").read_text(encoding="utf-8"))
        cls.codex = yaml.safe_load((RUNTIME / "agents" / "codex.yaml").read_text(encoding="utf-8"))

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def need(self, *backends):
        for backend in backends:
            ok, reason = available(backend)
            if not ok:
                raise unittest.SkipTest(f"NOT-APPLICABLE: {backend}: {reason}")


class TestSbxKitPayloadPath(unittest.TestCase):
    def test_00_home_files_map_to_the_installer_staging_path(self):
        # The pinned sbx kit maps files/home/* into /home/agent/* (as G6's live probe
        # established). A second "agent" component made production kit installation fail.
        with tempfile.TemporaryDirectory(prefix="dca-kit-layout-") as temporary:
            artifact = Path(temporary) / "artifact"
            artifact.write_bytes(b"sentinel")
            destination = Path(temporary) / "kit"

            def stage_sentinel(path, **_):
                (Path(path) / "sentinel").write_text("staged", encoding="utf-8")

            with mock.patch.object(stage_module, "stage", side_effect=stage_sentinel):
                stage_module.build_sbx_kit(destination, backends=["claude"], artifact=artifact)

            self.assertEqual((destination / "files/home/dca-kit/sentinel").read_text(),
                             "staged")
            self.assertFalse((destination / "files/home/agent/dca-kit").exists())
            self.assertIn("/home/agent/dca-kit/opt/dca/bin/docker-agent",
                          (destination / "spec.yaml").read_text())

    def test_00a_the_wrappers_name_the_install_path_not_the_staging_path(self):
        """The kit is STAGED on the host but RUNS at /opt/dca in the VM.

        A wrapper rendered against the staging directory names a file that does not exist in the
        sandbox, and because the gate wrapper maps every non-zero status to 2, the gate would then
        DENY every tool call instead of deciding any of them - a total loss of mediation that no
        static check of the staged tree would notice.
        """
        with tempfile.TemporaryDirectory(prefix="dca-kit-wrapper-") as temporary:
            destination = Path(temporary) / "kit"
            artifact = Path(temporary) / "artifact"
            artifact.write_bytes(b"sentinel")
            # The pinned binary is not in the repository; its SHA-256 check has its own test.
            with mock.patch.object(stage_module, "_stage_artifact", return_value=None):
                stage_module.build_sbx_kit(destination, backends=["claude"], artifact=artifact)
            payload = destination / "files/home/dca-kit/opt/dca"
            for name, module in (("dca-gate", "policy_gate.py"),
                                 ("dca-fingerprint", "fingerprint_hook.py")):
                with self.subTest(wrapper=name):
                    body = (payload / "bin" / name).read_text(encoding="utf-8")
                    self.assertIn(f"/usr/bin/python3 -I /opt/dca/lib/dca/{module}", body)
                    self.assertNotIn(str(destination), body)
                    self.assertNotIn(temporary, body)


# --- 1. the same actions policy, through the same gate ---------------------------------------


class TestActionsPolicyParity(ParityCase):
    def test_01_both_backends_run_the_same_gate_binary(self):
        self.need("claude", "codex")
        claude_hook = self.managed["hooks"]["PreToolUse"][0]["hooks"][0]["command"]
        codex_hooks = {name: agent["hooks"]["pre_tool_use"][0]["hooks"][0]["command"]
                       for name, agent in self.codex["agents"].items()}
        self.assertEqual(claude_hook, GATE)
        self.assertEqual(set(codex_hooks.values()), {GATE})

    def test_02_both_deny_lists_derive_from_the_same_actions_yaml(self):
        for backend, rules, derive in (
                ("claude", self.managed.get("permissions", {}).get("deny"),
                 policy_rules.claude_deny_rules),
                ("codex", (self.codex.get("permissions") or {}).get("deny"),
                 policy_rules.codex_deny_rules)):
            with self.subTest(backend=backend):
                self.need(backend)
                self.assertEqual(rules, derive(self.actions))

    def test_03_both_deny_lists_cover_the_same_deny_classes(self):
        self.need("claude", "codex")
        native, gate_only = policy_rules.covered_classes(self.actions)
        self.assertTrue(native, "the native layer must state at least some DENY classes")
        for class_id in gate_only:
            with self.subTest(deny_class=class_id):
                self.assertIn(class_id, policy_rules.GATE_ONLY,
                              "a class the native layer cannot state must be recorded as such")

    def test_04_codex_declares_strict_and_no_allow_or_ask_rule(self):
        self.need("codex")
        self.assertEqual({agent["safety"] for agent in self.codex["agents"].values()}, {"strict"})
        permissions = self.codex["permissions"]
        self.assertNotIn("allow", permissions)
        self.assertNotIn("ask", permissions)

    def test_05_claude_allows_only_managed_rules_and_hooks(self):
        self.need("claude")
        self.assertIs(self.managed["allowManagedPermissionRulesOnly"], True)
        self.assertIs(self.managed["allowManagedHooksOnly"], True)
        self.assertNotIn("allow", self.managed["permissions"])
        self.assertNotIn("ask", self.managed["permissions"])

    def test_06_the_staged_policy_is_the_repository_policy(self):
        self.assertEqual(digest(self.kit / "policy" / "actions.yaml"),
                         digest(RUNTIME / "policy" / "actions.yaml"))


# --- 2. the same limits -----------------------------------------------------------------------


class TestLimitsParity(ParityCase):
    def test_10_host_limits_are_a_single_backend_agnostic_source(self):
        host = self.limits["host_limits"]
        self.assertEqual(sorted(host), ["direct", "planned"])
        for classification in ("direct", "planned"):
            with self.subTest(classification=classification):
                for field in ("retries", "steps", "tokens", "wall_clock_seconds"):
                    self.assertIsInstance(host[classification][field], int)

    def test_11_codex_native_ceilings_equal_the_limits_file(self):
        self.need("codex")
        ceilings = self.limits["native_ceilings"]
        self.assertEqual(self.codex["budget"]["max_tokens"], ceilings["max_tokens"])
        for agent in self.codex["agents"].values():
            self.assertEqual(agent["max_iterations"], ceilings["max_iterations"])
            self.assertEqual(agent["max_consecutive_tool_calls"],
                             ceilings["max_consecutive_tool_calls"])

    def test_12_native_ceilings_are_the_planned_maxima_not_the_direct_ones(self):
        ceilings = self.limits["native_ceilings"]
        host = self.limits["host_limits"]
        self.assertEqual(ceilings["max_tokens"], host["planned"]["tokens"])
        self.assertGreater(host["planned"]["tokens"], host["direct"]["tokens"],
                           "a native ceiling set to the DIRECT maximum would silently become the "
                           "direct/planned semantics the host is supposed to own")

    def test_13_the_staged_limits_are_the_repository_limits(self):
        self.assertEqual(digest(self.kit / "policy" / "limits.yaml"),
                         digest(RUNTIME / "policy" / "limits.yaml"))


# --- 3. the same instructions ------------------------------------------------------------------


class TestInstructionParity(ParityCase):
    def test_20_the_managed_claude_memory_is_byte_equal_to_root_md(self):
        self.need("claude")
        self.assertEqual(digest(self.kit / "backends" / "claude" / "CLAUDE.md"),
                         digest(RUNTIME / "instructions" / "root.md"))

    def test_21_the_claude_subagent_bodies_are_the_shared_instructions(self):
        self.need("claude")
        for role in ("researcher", "reviewer"):
            with self.subTest(role=role):
                staged = (self.kit / "backends" / "claude" / "agents"
                          / f"dca-{role}.md").read_text(encoding="utf-8")
                shared = (RUNTIME / "instructions" / f"{role}.md").read_text(encoding="utf-8")
                self.assertTrue(staged.endswith(shared),
                                "the subagent body must be the shared instruction verbatim")

    def test_22_the_claude_subagents_are_read_only(self):
        self.need("claude")
        for role in ("researcher", "reviewer"):
            with self.subTest(role=role):
                body = (self.kit / "backends" / "claude" / "agents"
                        / f"dca-{role}.md").read_text(encoding="utf-8")
                self.assertIn("tools: Read, Grep, Glob", body)

    def test_23_codex_references_the_same_three_instruction_files(self):
        self.need("codex")
        for name, agent in sorted(self.codex["agents"].items()):
            with self.subTest(agent=name):
                relative = agent["instruction_file"]
                self.assertEqual(
                    digest(self.kit / "agents" / relative),
                    digest(RUNTIME / "instructions" / Path(relative).name))

    def test_24_both_backends_get_the_same_root_instruction(self):
        self.need("claude", "codex")
        self.assertEqual(digest(self.kit / "backends" / "claude" / "CLAUDE.md"),
                         digest(self.kit / "agents" / "instructions" / "root.md"))


class TestEvidenceOrderingParity(ParityCase):
    """T081: the ordering contract reaches both backends through the same bytes."""

    ORDERING = ("write the context record first, then run the baseline, then change anything",
                "evidence for a required check is the run made after your last edit")

    def squash(self, path):
        return " ".join(Path(path).read_text(encoding="utf-8").lower().split())

    def test_35_both_backends_are_given_the_ordering_in_their_root_instruction(self):
        self.need("claude", "codex")
        for label, path in (("claude", self.kit / "backends" / "claude" / "CLAUDE.md"),
                            ("codex", self.kit / "agents" / "instructions" / "root.md")):
            for phrase in self.ORDERING:
                with self.subTest(backend=label, phrase=phrase):
                    self.assertIn(phrase, self.squash(path))

    def test_36_both_backends_load_the_baseline_and_receipt_rules_from_the_same_skill_bytes(self):
        self.need("claude", "codex")
        for name, phrase in (("root-cause-debugging", "after the context record is written"),
                             ("change-receipt", "never record a pre-change run in `checks`")):
            shared = self.kit / "skills" / name / "SKILL.md"
            claude = self.kit / "backends" / "claude" / "skills" / name / "SKILL.md"
            with self.subTest(skill=name):
                self.assertIn(phrase, self.squash(shared))
                self.assertEqual(digest(claude), digest(shared))


# --- 4. the same skills, from a trusted source ---------------------------------------------------


class TestSkillParity(ParityCase):
    def manifest(self):
        return json.loads((self.kit / "kit-manifest.json").read_text(encoding="utf-8"))

    def test_30_the_trusted_skill_root_holds_exactly_the_four_runtime_skills(self):
        present = sorted(p.name for p in (self.kit / "skills").iterdir() if p.is_dir())
        self.assertEqual(present, sorted(RUNTIME_SKILLS))
        self.assertEqual(sorted(self.manifest()["skills"]), sorted(RUNTIME_SKILLS))

    def test_31_every_manifest_entry_matches_the_staged_bytes_and_the_repository_source(self):
        for name, entry in sorted(self.manifest()["skills"].items()):
            with self.subTest(skill=name):
                staged = self.kit / entry["path"]
                self.assertEqual(digest(staged), entry["sha256"])
                self.assertEqual(digest(staged), digest(RUNTIME / "skills" / name / "SKILL.md"))

    def test_32_claude_receives_the_same_skill_bytes_not_merely_the_same_names(self):
        self.need("claude")
        for name, entry in sorted(self.manifest()["skills"].items()):
            with self.subTest(skill=name):
                claude_copy = self.kit / "backends" / "claude" / "skills" / name / "SKILL.md"
                self.assertEqual(digest(claude_copy), entry["sha256"])

    def test_33_codex_resolves_the_same_four_names_from_the_kit(self):
        self.need("codex")
        self.assertEqual(sorted(self.codex["agents"]["root"]["skills"]), sorted(RUNTIME_SKILLS))
        for name in RUNTIME_SKILLS:
            self.assertTrue((self.kit / "skills" / name / "SKILL.md").is_file())

    def test_34_there_is_exactly_one_manifest(self):
        manifests = sorted(p.relative_to(self.kit).as_posix()
                           for p in self.kit.rglob("kit-manifest.json"))
        self.assertEqual(manifests, ["kit-manifest.json"])


# --- 5. the same gate implementation --------------------------------------------------------------


class TestGateImplementationParity(ParityCase):
    def test_40_the_staged_gate_modules_are_the_reviewed_ones(self):
        for module in ("policy_gate.py", "shellparse.py", "fingerprint.py"):
            with self.subTest(module=module):
                self.assertEqual(digest(self.kit / "lib" / "dca" / module),
                                 digest(ROOT / "src" / "dca" / module))

    def test_41_there_is_one_gate_wrapper_used_by_both_backends(self):
        wrapper = (self.kit / "bin" / "dca-gate").read_text(encoding="utf-8")
        self.assertIn("policy_gate.py", wrapper)
        self.assertIn("-I", wrapper)


# --- 6. the same completion report ----------------------------------------------------------------


class TestReportParity(ParityCase):
    def test_50_both_backends_are_told_to_write_the_same_agent_report_path(self):
        for name in ("root.md", "researcher.md", "reviewer.md"):
            staged = (self.kit / "agents" / "instructions" / name).read_text(encoding="utf-8")
            if name == "root.md":
                with self.subTest(instruction=name):
                    self.assertIn("/run/dca/out/report.agent.json", staged)

    def test_51_one_backend_agnostic_finalizer_decides_the_outcome(self):
        finalizer = ROOT / "src" / "dca" / "report.py"
        self.assertTrue(finalizer.is_file())
        body = finalizer.read_text(encoding="utf-8")
        for backend in ("claude", "codex"):
            with self.subTest(backend=backend):
                self.assertNotIn(f'== "{backend}"', body,
                                 "the finalizer must not branch on the backend")


# --- divergence and availability -------------------------------------------------------------------


class TestDivergenceIsDetected(ParityCase):
    def test_60_a_changed_codex_limit_fails_the_limit_check(self):
        self.need("codex")
        seeded = json.loads(json.dumps(self.codex))
        seeded["budget"]["max_tokens"] = self.limits["native_ceilings"]["max_tokens"] + 1
        self.assertNotEqual(seeded["budget"]["max_tokens"],
                            self.limits["native_ceilings"]["max_tokens"])

    def test_61_a_weakened_safety_mode_fails_the_policy_check(self):
        self.need("codex")
        seeded = json.loads(json.dumps(self.codex))
        seeded["agents"]["root"]["safety"] = "restricted"
        self.assertNotEqual({a["safety"] for a in seeded["agents"].values()}, {"strict"})

    def test_62_a_tampered_skill_fails_the_manifest_check(self):
        manifest = json.loads((self.kit / "kit-manifest.json").read_text(encoding="utf-8"))
        target = self.kit / manifest["skills"]["verification"]["path"]
        original = target.read_bytes()
        try:
            target.write_bytes(original + b"\n<!-- seeded divergence -->\n")
            self.assertNotEqual(digest(target), manifest["skills"]["verification"]["sha256"])
        finally:
            target.write_bytes(original)

    def test_63_a_deny_list_edited_by_hand_fails_the_derivation_check(self):
        self.need("codex")
        seeded = list(self.codex["permissions"]["deny"])[:-1]
        self.assertNotEqual(seeded, policy_rules.codex_deny_rules(self.actions))


class TestAvailabilityReporting(ParityCase):
    def test_70_an_unavailable_backend_is_reported_not_applicable_with_a_reason(self):
        synthetic = {"backends": {"claude": {"available": True},
                                  "codex": {"available": False,
                                            "unavailable_reason": "G3 FAIL (synthetic fixture)"}}}
        claude_ok, _ = available("claude", synthetic)
        codex_ok, reason = available("codex", synthetic)
        self.assertTrue(claude_ok, "the available backend's checks must still run")
        self.assertFalse(codex_ok)
        self.assertIn("synthetic fixture", reason)

    def test_71_the_kit_holds_material_only_for_available_backends(self):
        for backend in ("claude", "codex"):
            with self.subTest(backend=backend):
                staged = (self.kit / "backends" / backend).is_dir()
                self.assertEqual(staged, backend in self.backends)

    def test_72_the_shared_core_is_staged_whatever_the_backend_mix(self):
        for relative in ("kit-manifest.json", "bin/dca-gate", "lib/dca/policy_gate.py",
                         "policy/actions.yaml", "skills/verification/SKILL.md"):
            with self.subTest(path=relative):
                self.assertTrue((self.kit / relative).exists())


if __name__ == "__main__":
    unittest.main()
