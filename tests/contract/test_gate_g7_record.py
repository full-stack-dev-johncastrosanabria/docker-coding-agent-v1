"""Contract tests for the G7 recorder and the shared SSH detector (tasks.md T010).

They run the decision logic against synthetic captures in a temporary work directory: no
sandbox, no sbx command, no change to the real runtime/versions.yaml. The shapes mirror what
the sbx version pinned by G0 emits on this host.

The invariant under test is the one G7 proves: **no usable forwarded SSH-agent endpoint exists
and no host SSH agent is reachable.** A dangling SSH_AUTH_SOCK is acceptable only with every
piece of corroborating evidence; an agent that answers at all is not.
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
SANDBOX = "dca-g7-ssh"


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class G7Recorder(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.record_module = _load("g7_record", GATES / "G7" / "record.py")
        cls.preflight = _load("dca_preflight_test", GATES / "preflight.py")
        cls.rules = _load("eligibility_rules", GATES / "eligibility_rules.py")
        cls.evidence_schema = json.loads((GATES / "evidence.schema.json").read_text(encoding="utf-8"))

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.work = self.tmp / "work"
        self.work.mkdir()
        self.recorded = self.tmp / "recorded"
        shutil.copytree(GATES / "G7" / "recorded", self.recorded)
        self.evidence_path = self.tmp / "G7.json"
        pins = json.loads((ROOT / "runtime" / "versions.yaml").read_text(encoding="utf-8"))
        self.pinned_base = pins["sandbox_bases"]["claude"]["base"]
        self.pinned_digest = pins["sandbox_bases"]["claude"]["version"]
        repository, _, tag = self.pinned_base.rpartition(":")

        # The state this host actually shows: a dangling SSH_AUTH_SOCK and no reachable agent.
        self.probe = {
            "ssh_auth_sock": "set",
            "ssh_auth_sock_path": "/run/ssh-agent.sock",
            "ssh_auth_sock_exists": "no",
            "ssh_auth_sock_is_socket": "no",
            "ssh_auth_sock_dir_exists": "yes",
            "pid1_has_ssh_auth_sock": "1",
            "image_declares_ssh": "0",
            "ssh_agent_pid": "unset",
            "ssh_env_names": "1",
            "ssh_add_present": "yes",
            "ssh_add_exit": "2",
            "ssh_add_cannot_connect": "yes",
            "ssh_add_reports_identities": "no",
            "agent_sockets": "0",
            "socket_paths": "",
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
            "templates.json": {
                "images": [
                    {
                        "id": self.pinned_digest.removeprefix("sha256:")[:12],
                        "repository": f"docker.io/{repository}",
                        "tag": tag,
                        "size": 936879795,
                    }
                ]
            },
            "ls-after.json": {"sandboxes": []},
        }
        self.obs = {
            "host_ssh_auth_sock_set": "yes",
            "sbx_env_ssh_auth_sock": "removed",
            "pf_version_exit": "0",
            "pf_ssh_forwarding_exit": "0",
            "pf_ssh_socket_exit": "0",
            "pf_policy_exit": "0",
            "pf_ls_exit": "0",
            "template_ls_exit": "0",
            f"create_{SANDBOX}_exit": "0",
            f"probe_{SANDBOX}_exit": "0",
            f"rm_{SANDBOX}_exit": "0",
            "ls_after_exit": "0",
            f"image_{SANDBOX}": self.pinned_base,
            f"workspace_line_{SANDBOX}": "none · no workspace bind mount",
        }

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
        (self.work / f"probe-{SANDBOX}.env").write_text(
            "".join(f"{k}={v}\n" for k, v in self.probe.items()), encoding="utf-8"
        )
        obs_path = self.tmp / "observations.env"
        obs_path.write_text("".join(f"{k}={v}\n" for k, v in self.obs.items()), encoding="utf-8")
        return obs_path

    def results(self):
        status, criteria = self.record_module.record(
            str(self.write()),
            str(self.work),
            str(ROOT / "runtime" / "versions.yaml"),
            str(self.evidence_path),
            str(self.recorded),
        )
        return status, {row["id"]: row["result"] for row in criteria}

    def assert_fails(self, criterion):
        status, results = self.results()
        self.assertEqual(status, "FAIL", results)
        self.assertEqual(results[criterion], "FAIL")

    def isolation(self):
        return self.preflight.ssh_agent_isolation_failures(self.probe)

    # --- the invariant ----------------------------------------------------------------------

    def test_dangling_socket_with_all_corroborating_evidence_is_safe(self):
        self.assertEqual(self.isolation(), [])
        status, results = self.results()
        self.assertEqual(status, "PASS", results)
        self.assertEqual(results["G7.3"], "PASS")

    def test_unset_socket_is_safe(self):
        self.probe.update({"ssh_auth_sock": "unset", "ssh_auth_sock_path": ""})
        self.assertEqual(self.isolation(), [])
        status, _ = self.results()
        self.assertEqual(status, "PASS")

    def test_a_real_socket_fails(self):
        for label, change in (
            ("path exists", {"ssh_auth_sock_exists": "yes"}),
            ("path is a socket", {"ssh_auth_sock_is_socket": "yes"}),
            ("both", {"ssh_auth_sock_exists": "yes", "ssh_auth_sock_is_socket": "yes"}),
        ):
            with self.subTest(case=label):
                self.setUp()
                self.probe.update(change)
                self.assertNotEqual(self.isolation(), [])
                self.assert_fails("G7.3")

    def test_an_agent_with_no_identities_fails(self):
        # "The agent has no identities" means an agent answered: it is reachable.
        self.probe.update({
            "ssh_add_exit": "1",
            "ssh_add_cannot_connect": "no",
            "ssh_add_reports_identities": "yes",
        })
        reasons = self.isolation()
        self.assertTrue(any("no identities loaded" in r for r in reasons), reasons)
        self.assert_fails("G7.3")

    def test_a_successful_ssh_add_fails(self):
        self.probe.update({
            "ssh_add_exit": "0",
            "ssh_add_cannot_connect": "no",
            "ssh_add_reports_identities": "yes",
        })
        self.assertNotEqual(self.isolation(), [])
        status, results = self.results()
        self.assertEqual(status, "FAIL")
        self.assertEqual(results["G7.3"], "FAIL")
        self.assertEqual(results["G7.4"], "FAIL")

    def test_ssh_add_that_cannot_connect_is_safe(self):
        self.probe.update({"ssh_add_exit": "2", "ssh_add_cannot_connect": "yes"})
        self.assertEqual(self.isolation(), [])

    def test_a_discovered_agent_socket_fails(self):
        self.probe.update({"agent_sockets": "1", "socket_paths": "/tmp/ssh-XXXX/agent.42"})
        reasons = self.isolation()
        self.assertTrue(any("candidate agent sockets" in r for r in reasons), reasons)
        status, results = self.results()
        self.assertEqual(status, "FAIL")
        self.assertEqual(results["G7.5"], "FAIL")

    def test_an_agent_pid_fails(self):
        self.probe["ssh_agent_pid"] = "set"
        self.assertNotEqual(self.isolation(), [])
        self.assert_fails("G7.3")

    def test_missing_or_malformed_probe_evidence_fails_closed(self):
        for label, change in (
            ("no socket state", {"ssh_auth_sock": ""}),
            ("unknown socket state", {"ssh_auth_sock": "maybe"}),
            ("existence not reported", {"ssh_auth_sock_exists": ""}),
            ("socketness not reported", {"ssh_auth_sock_is_socket": "unknown"}),
            ("ssh-add absent", {"ssh_add_present": "no", "ssh_add_cannot_connect": "absent"}),
            ("connect state not reported", {"ssh_add_cannot_connect": ""}),
            ("socket count not reported", {"agent_sockets": ""}),
            ("agent pid not reported", {"ssh_agent_pid": ""}),
        ):
            with self.subTest(case=label):
                self.setUp()
                self.probe.update(change)
                self.assertNotEqual(self.isolation(), [], label)
                self.assert_fails("G7.3")
        self.setUp()
        self.assertNotEqual(self.preflight.ssh_agent_isolation_failures({}), [])

    def test_an_unsuccessful_probe_leaves_the_in_sandbox_criteria_not_run(self):
        self.obs[f"probe_{SANDBOX}_exit"] = "1"
        status, results = self.results()
        self.assertEqual(status, "FAIL")
        for criterion in ("G7.3", "G7.4", "G7.5"):
            self.assertEqual(results[criterion], "NOT-RUN")

    # --- exact pinned base -------------------------------------------------------------------

    def test_exact_pinned_base_passes(self):
        status, results = self.results()
        self.assertEqual(status, "PASS", results)
        self.assertEqual(results["G7.2"], "PASS")

    def test_same_tag_with_a_different_digest_fails(self):
        self.files["templates.json"]["images"][0]["id"] = "deadbeef1234"
        self.assert_fails("G7.2")

    def test_wrong_repository_or_tag_fails(self):
        for label, change in (
            ("wrong repository", {"repository": "docker.io/evil/sandbox-templates"}),
            ("wrong tag", {"tag": "docker-agent-docker"}),
        ):
            with self.subTest(case=label):
                self.setUp()
                self.files["templates.json"]["images"][0].update(change)
                self.assert_fails("G7.2")

    def test_missing_template_store_or_reference_fails(self):
        for label, break_it in (
            ("template store unreadable", lambda: self.files.__setitem__("templates.json", {"x": []})),
            ("template store empty", lambda: self.files["templates.json"].__setitem__("images", [])),
            ("store read failed", lambda: self.obs.__setitem__("template_ls_exit", "1")),
            ("sbx resolved another base", lambda: self.obs.__setitem__(
                f"image_{SANDBOX}", "docker/sandbox-templates:docker-agent-docker")),
            ("workspace was bind mounted", lambda: self.obs.__setitem__(
                f"workspace_line_{SANDBOX}", "/Users/dev/repo · bind mount")),
        ):
            with self.subTest(case=label):
                self.setUp()
                break_it()
                self.assert_fails("G7.2")

    # --- the read-only detector ----------------------------------------------------------------

    def test_detector_refuses_both_recorded_negative_states(self):
        verdicts = self.record_module.recorded_refusals(str(self.recorded))
        self.assertTrue(verdicts["forwarding-enabled"])
        self.assertTrue(verdicts["fixed-socket"])
        status, results = self.results()
        self.assertEqual(status, "PASS", results)
        self.assertEqual(results["G7.6"], "PASS")

    def test_detector_requires_both_settings_to_be_readable(self):
        for label, change in (
            ("forwarding unreadable", {"pf-ssh-forwarding.json": {"key": "other", "value": False}}),
            ("socket unreadable", {"pf-ssh-socket.json": {"unexpected": True}}),
            ("forwarding enabled", {"pf-ssh-forwarding.json": {
                "key": "ssh.agentForwardingEnabled", "value": True, "source": "default"}}),
            ("fixed socket set", {"pf-ssh-socket.json": {
                "key": "ssh.agentSocketPath", "value": "/tmp/agent.sock", "source": "override"}}),
        ):
            with self.subTest(case=label):
                self.setUp()
                self.files.update(change)
                self.assert_fails("G7.0b")

    def test_detector_does_not_claim_a_fixed_socket_bypasses_the_flag(self):
        reasons = self.preflight.ssh_forwarding_refusals(
            {"key": "ssh.agentForwardingEnabled", "value": False},
            {"key": "ssh.agentSocketPath", "value": "/tmp/agent.sock"},
        )
        self.assertEqual(len(reasons), 1)
        self.assertNotIn("bypass", reasons[0].lower())
        self.assertNotIn("even with forwarding disabled", reasons[0].lower())
        self.assertIn("ssh.agentSocketPath", reasons[0])

    # --- the rest of the gate --------------------------------------------------------------------

    def test_sbx_calls_must_run_without_the_agent_socket(self):
        self.obs["sbx_env_ssh_auth_sock"] = "present"
        self.assert_fails("G7.1")

    def test_cleanup_is_required(self):
        self.setUp()
        self.obs[f"rm_{SANDBOX}_exit"] = "1"
        self.assert_fails("G7.7")
        self.setUp()
        self.files["ls-after.json"] = {"sandboxes": [{"name": SANDBOX}]}
        self.assert_fails("G7.7")

    def test_evidence_is_schema_valid_and_provenance_current(self):
        self.results()
        evidence = json.loads(self.evidence_path.read_text(encoding="utf-8"))
        versions = json.loads((ROOT / "runtime" / "versions.yaml").read_text(encoding="utf-8"))
        self.assertEqual(
            [e.message for e in jsonschema.Draft202012Validator(self.evidence_schema).iter_errors(evidence)],
            [],
        )
        self.assertEqual(evidence["provenance"], self.rules.evidence_provenance("G7", versions))
        self.assertEqual(self.rules.evidence_problems(evidence, versions), [])
        text = json.dumps(evidence).lower()
        for word in ("token", "secret", "password", "bearer", "cookie"):
            self.assertNotIn(word, text)

    def test_run_sh_removes_the_agent_socket_and_cleans_up_before_failing(self):
        script = (GATES / "G7" / "run.sh").read_text(encoding="utf-8")
        body = "\n".join(line for line in script.splitlines() if not line.lstrip().startswith("#"))
        self.assertIn("env -u SSH_AUTH_SOCK sbx", body)
        self.assertNotIn("--all", body)
        self.assertIn("sbx rm --force", body.replace("run_sbx rm --force", "sbx rm --force"))
        stop = body.index("stop() {")
        stop_body = body[stop:body.index("}", body.index("record.py", stop))]
        self.assertLess(stop_body.index("cleanup"), stop_body.index("record.py"))


if __name__ == "__main__":
    unittest.main()
