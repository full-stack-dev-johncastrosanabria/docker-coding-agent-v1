"""Contract tests for the G8 recorder (tasks.md T011, gates/G8/record.py).

They run the decision logic against synthetic captures in a temporary work directory: no
sandbox, no sbx command, no change to the real runtime/versions.yaml. The shapes mirror what
the sbx version pinned by G0 emits on this host.

G8's scope here is shared-store isolation at the probe stage: the store is not mounted, nothing
foreign appears in a skill directory, and the G6 kit's directory holds exactly its two probe
skills. The final four runtime skills belong to T056 and production conformance, not to G8.
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
KIT_SKILLS_DIR = "/opt/dca/skills"


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class G8Recorder(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.record_module = _load("g8_record", GATES / "G8" / "record.py")
        cls.rules = _load("eligibility_rules", GATES / "eligibility_rules.py")
        cls.evidence_schema = json.loads((GATES / "evidence.schema.json").read_text(encoding="utf-8"))

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.work = self.tmp / "work"
        self.work.mkdir()
        self.evidence_path = self.tmp / "G8.json"
        self.versions_path = ROOT / "runtime" / "versions.yaml"
        pins = json.loads(self.versions_path.read_text(encoding="utf-8"))
        self.pins = pins

        templates = []
        for backend in ("claude", "codex"):
            base = pins["sandbox_bases"][backend]["base"]
            digest = pins["sandbox_bases"][backend]["version"]
            repository, _, tag = base.rpartition(":")
            templates.append({
                "id": digest.removeprefix("sha256:")[:12],
                "repository": f"docker.io/{repository}",
                "tag": tag,
                "size": 1,
            })

        self.probes = {
            "dca-g8-claude": self.probe(),
            "dca-g8-codex": self.probe(),
            # Controls: the Claude base mounts the store at readonly, the docker-agent base
            # exposes no agent skills directory to mount into. Both were observed.
            "dca-g8-claude-control": self.probe(
                mount_skill_targets="/home/agent/.claude/skills ",
                extra_dirs=[("/home/agent/.claude/skills", "", "1")],
            ),
            "dca-g8-codex-control": self.probe(),
        }
        self.files = {
            "pf-version.json": {
                "client": {"version": pins["sbx"]["exact"]},
                "server": {"state": "running", "version": pins["sbx"]["exact"]},
            },
            "pf-ssh-forwarding.json": {
                "key": "ssh.agentForwardingEnabled", "value": False, "source": "override"
            },
            "pf-ssh-socket.json": {"key": "ssh.agentSocketPath", "value": "", "source": "default"},
            "pf-policy.json": {"rules": [self.bootstrap_rule()]},
            "templates.json": {"images": templates},
            "host-skills.json": {"store": "/host/agent-skills", "skills": []},
            "ls-after.json": {"sandboxes": []},
        }
        self.obs = {
            "sbx_env_ssh_auth_sock": "removed",
            "pf_version_exit": "0", "pf_ssh_forwarding_exit": "0", "pf_ssh_socket_exit": "0",
            "pf_policy_exit": "0", "pf_ls_exit": "0", "template_ls_exit": "0",
            "host_skills_exit": "0", "kit_path": "gates/G6/work/kit", "ls_after_exit": "0",
        }
        for backend, sandbox in (("claude", "dca-g8-claude"), ("codex", "dca-g8-codex")):
            control = f"{sandbox}-control"
            self.obs.update({
                f"create_{sandbox}_exit": "0", f"probe_{sandbox}_exit": "0",
                f"rm_{sandbox}_exit": "0",
                f"create_{control}_exit": "0", f"probe_{control}_exit": "0",
                f"rm_{control}_exit": "0",
                f"image_{sandbox}": pins["sandbox_bases"][backend]["base"],
                f"workspace_line_{sandbox}": "none · no workspace bind mount",
            })

    @staticmethod
    def probe(mount_skill_targets="", extra_dirs=()):
        """A probe capture: the kit's skill directory plus any extra discovered directory."""
        dirs = [(KIT_SKILLS_DIR, "dca-probe-one dca-probe-two ", "0")] + list(extra_dirs)
        out = {
            "mount_total": "18",
            "mount_skill_targets": mount_skill_targets,
            "skills_dirs": " ".join(path for path, _, _ in dirs) + " ",
        }
        for index, (path, entries, mounted) in enumerate(dirs, start=1):
            out[f"skills_dir_{index}"] = path
            out[f"skills_dir_{index}_entries"] = entries
            out[f"skills_dir_{index}_mounted"] = mounted
        out["skills_dir_count"] = str(len(dirs))
        out["kit_skills_entries"] = "dca-probe-one dca-probe-two "
        out["speckit_hits"] = "0"
        out["probe_complete"] = "yes"
        return out

    @staticmethod
    def bootstrap_rule(**overrides):
        rule = {
            "id": "default-deny-all", "scope": "global", "applies_to": "all",
            "resource_type": "network", "decision": "deny", "resources": ["**"],
            "origin": "local", "layer": "local", "status": "active",
        }
        rule.update(overrides)
        return rule

    # --- helpers ---------------------------------------------------------------------------

    def write(self):
        for name, document in self.files.items():
            (self.work / name).write_text(json.dumps(document), encoding="utf-8")
        for sandbox, probe in self.probes.items():
            (self.work / f"probe-{sandbox}.env").write_text(
                "".join(f"{k}={v}\n" for k, v in probe.items()), encoding="utf-8"
            )
        obs_path = self.tmp / "observations.env"
        obs_path.write_text("".join(f"{k}={v}\n" for k, v in self.obs.items()), encoding="utf-8")
        return obs_path

    def results(self):
        status, criteria = self.record_module.record(
            str(self.write()), str(self.work), str(self.versions_path), str(self.evidence_path)
        )
        return status, {row["id"]: row["result"] for row in criteria}

    def assert_fails(self, criterion):
        status, results = self.results()
        self.assertEqual(status, "FAIL", results)
        self.assertEqual(results[criterion], "FAIL")

    # --- the baseline -------------------------------------------------------------------------

    def test_shared_store_absent_and_only_probe_skills_passes(self):
        status, results = self.results()
        self.assertEqual(status, "PASS", results)
        self.assertTrue(all(r == "PASS" for r in results.values()), results)

    # --- shared-store isolation -----------------------------------------------------------------

    def test_a_mounted_shared_store_fails(self):
        for label, change in (
            ("mount target under a skills path", {"mount_skill_targets": "/home/agent/.claude/skills "}),
            ("the kit directory itself is a mount", {"skills_dir_1_mounted": "1"}),
        ):
            with self.subTest(case=label):
                self.setUp()
                self.probes["dca-g8-claude"].update(change)
                self.assertNotEqual(
                    self.record_module.shared_store_failures(self.probes["dca-g8-claude"]), []
                )
                self.assert_fails("G8.claude.store")

    def test_a_mounted_skills_directory_elsewhere_fails(self):
        self.probes["dca-g8-codex"] = self.probe(
            mount_skill_targets="/home/agent/.config/skills ",
            extra_dirs=[("/home/agent/.config/skills", "", "1")],
        )
        self.assert_fails("G8.codex.store")

    # --- skill contents ---------------------------------------------------------------------------

    def test_an_extra_skill_fails(self):
        for label, change in (
            ("extra entry in the kit directory",
             {"skills_dir_1_entries": "dca-probe-one dca-probe-two extra-skill "}),
            ("kit directory listing disagrees with the kit entries",
             {"skills_dir_1_entries": "dca-probe-one "}),
        ):
            with self.subTest(case=label):
                self.setUp()
                self.probes["dca-g8-claude"].update(change)
                self.assertNotEqual(
                    self.record_module.skill_content_failures(self.probes["dca-g8-claude"]), []
                )
                self.assert_fails("G8.claude.skills")

    def test_a_missing_probe_skill_fails(self):
        self.probes["dca-g8-claude"].update({"skills_dir_1_entries": "dca-probe-two "})
        self.assert_fails("G8.claude.skills")

    def test_a_skill_injected_elsewhere_fails(self):
        # A host or shared skill appearing in any other discovered directory is a failure, even
        # when the directory is not itself a mount point.
        self.probes["dca-g8-claude"] = self.probe(
            extra_dirs=[("/home/agent/.claude/skills", "host-shared-skill ", "0")]
        )
        reasons = self.record_module.skill_content_failures(self.probes["dca-g8-claude"])
        self.assertTrue(any("not empty" in r for r in reasons), reasons)
        self.assert_fails("G8.claude.skills")

    def test_a_speckit_construction_skill_fails(self):
        for label, change in (
            ("speckit path found", {"speckit_hits": "1"}),
            ("speckit skill in the kit directory",
             {"skills_dir_1_entries": "dca-probe-one dca-probe-two speckit-plan ", "speckit_hits": "1"}),
        ):
            with self.subTest(case=label):
                self.setUp()
                self.probes["dca-g8-codex"].update(change)
                self.assert_fails("G8.codex.skills")

    def test_the_kit_directory_must_exist_in_the_vm(self):
        self.probes["dca-g8-claude"] = self.probe(extra_dirs=())
        self.probes["dca-g8-claude"].update({
            "skills_dir_1": "/home/agent/.claude/skills",
            "skills_dir_1_entries": "",
            "kit_skills_entries": "",
        })
        reasons = self.record_module.skill_content_failures(self.probes["dca-g8-claude"])
        self.assertTrue(any("was not found" in r for r in reasons), reasons)
        self.assert_fails("G8.claude.skills")

    # --- fail closed ---------------------------------------------------------------------------------

    def test_malformed_or_missing_probe_evidence_fails_closed(self):
        for label, change in (
            ("probe incomplete", {"probe_complete": "no"}),
            ("directory count not a number", {"skills_dir_count": ""}),
            ("directory path missing", {"skills_dir_1": ""}),
            ("mount state not a number", {"skills_dir_1_mounted": "unknown"}),
            ("mount table not reported", {"mount_total": ""}),
            ("speckit count not reported", {"speckit_hits": ""}),
        ):
            with self.subTest(case=label):
                self.setUp()
                self.probes["dca-g8-claude"].update(change)
                status, results = self.results()
                self.assertEqual(status, "FAIL")
                self.assertTrue(
                    results["G8.claude.store"] == "FAIL" or results["G8.claude.skills"] == "FAIL",
                    results,
                )
        self.setUp()
        self.assertIsNone(self.record_module.skill_directories({}))

    def test_an_unsuccessful_probe_leaves_the_in_sandbox_criteria_not_run(self):
        self.obs["probe_dca-g8-claude_exit"] = "1"
        status, results = self.results()
        self.assertEqual(status, "FAIL")
        for criterion in ("G8.claude.store", "G8.claude.skills"):
            self.assertEqual(results[criterion], "NOT-RUN")

    # --- pinned identity and provenance ------------------------------------------------------------

    def test_wrong_template_identity_fails(self):
        for label, break_it in (
            ("cached id is another image",
             lambda: self.files["templates.json"]["images"][0].update({"id": "deadbeef1234"})),
            ("wrong repository",
             lambda: self.files["templates.json"]["images"][0].update({"repository": "docker.io/evil/x"})),
            ("template store unreadable",
             lambda: self.files.__setitem__("templates.json", {"unexpected": []})),
            ("store read failed", lambda: self.obs.__setitem__("template_ls_exit", "1")),
            ("sbx resolved another base",
             lambda: self.obs.__setitem__("image_dca-g8-claude", "docker/sandbox-templates:other")),
            ("workspace bind mounted",
             lambda: self.obs.__setitem__("workspace_line_dca-g8-claude", "/Users/dev/repo · bind mount")),
        ):
            with self.subTest(case=label):
                self.setUp()
                break_it()
                self.assert_fails("G8.claude.create")

    def test_stale_provenance_is_detected(self):
        self.results()
        evidence = json.loads(self.evidence_path.read_text(encoding="utf-8"))
        versions = json.loads(self.versions_path.read_text(encoding="utf-8"))
        self.assertEqual(self.rules.evidence_problems(evidence, versions), [])
        # Any changed pin makes this evidence stale: G8 is bound by the digest of the whole file.
        drifted = json.loads(json.dumps(versions))
        drifted["sandbox_bases"]["claude"]["version"] = "sha256:" + "f" * 64
        problems = self.rules.evidence_problems(evidence, drifted)
        self.assertTrue(any("stale" in p for p in problems), problems)

    def test_preflight_drift_blocks_everything_after_it(self):
        for label, change in (
            ("network baseline drifted", {"pf-policy.json": {"rules": [
                self.bootstrap_rule(), self.bootstrap_rule(id="extra-allow", decision="allow")]}}),
            ("ssh forwarding re-enabled", {"pf-ssh-forwarding.json": {
                "key": "ssh.agentForwardingEnabled", "value": True, "source": "default"}}),
            ("version pin drifted", {"pf-version.json": {
                "client": {"version": "v0.44.0"}, "server": {"state": "running", "version": "v0.44.0"}}}),
        ):
            with self.subTest(case=label):
                self.setUp()
                self.files.update(change)
                status, results = self.results()
                self.assertEqual(status, "FAIL")
                self.assertEqual(results["G8.claude.create"], "NOT-RUN")

    def test_host_ssh_auth_sock_must_be_removed_from_sbx_calls(self):
        self.obs["sbx_env_ssh_auth_sock"] = "present"
        self.assert_fails("G8.0b")

    # --- cleanup and evidence -----------------------------------------------------------------------

    def test_cleanup_is_required(self):
        self.setUp()
        self.obs["rm_dca-g8-claude-control_exit"] = "1"
        self.assert_fails("G8.claude.cleanup")
        self.setUp()
        self.files["ls-after.json"] = {"sandboxes": [{"name": "dca-g8-codex"}]}
        self.assert_fails("G8.clean")

    def test_evidence_is_schema_valid_and_scoped_to_the_probe_stage(self):
        self.results()
        evidence = json.loads(self.evidence_path.read_text(encoding="utf-8"))
        self.assertEqual(
            [e.message for e in jsonschema.Draft202012Validator(self.evidence_schema).iter_errors(evidence)],
            [],
        )
        notes = evidence["notes"]
        self.assertIn("does not", notes.replace("not of this gate", "does not"))
        self.assertIn("production kit (T056)", notes)
        self.assertNotIn("repository-navigation", json.dumps(evidence))
        text = json.dumps(evidence).lower()
        for word in ("token", "secret", "password", "bearer", "cookie"):
            self.assertNotIn(word, text)

    def test_control_explains_a_missing_mount_by_base_not_by_an_empty_store(self):
        # The Claude control mounts the store from the same host store the codex control sees,
        # so a missing mount on one base must not be blamed on the store being empty.
        self.results()
        evidence = json.loads(self.evidence_path.read_text(encoding="utf-8"))
        codex = next(r["evidence_ref"] for r in evidence["criteria"] if r["id"] == "G8.codex.control")
        claude = next(r["evidence_ref"] for r in evidence["criteria"] if r["id"] == "G8.claude.control")
        self.assertIn("differential", claude)
        self.assertIn("base-specific rather than an artifact of the store being empty", codex)

    def test_run_sh_removes_the_agent_socket_and_cleans_up_before_failing(self):
        script = (GATES / "G8" / "run.sh").read_text(encoding="utf-8")
        body = "\n".join(line for line in script.splitlines() if not line.lstrip().startswith("#"))
        self.assertIn("env -u SSH_AUTH_SOCK sbx", body)
        self.assertNotIn("--all", body)
        self.assertNotIn("sbx reset", body)
        self.assertIn("--skills off", body)
        self.assertIn("--skills readonly", body)
        stop = body.index("stop() {")
        stop_body = body[stop:body.index("}", body.index("record.py", stop))]
        self.assertLess(stop_body.index("cleanup"), stop_body.index("record.py"))


if __name__ == "__main__":
    unittest.main()
