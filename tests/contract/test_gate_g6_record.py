"""Contract tests for the G6 recorder (tasks.md T009, gates/G6/record.py).

They run the recorder's decision logic against synthetic captures in a temporary work
directory: no sandbox is created, no sbx command runs, and the real runtime/versions.yaml is
never written. The shapes mirror what the sbx v0.43.0 pinned by G0 actually emits.
"""

import contextlib
import importlib.util
import io
import json
import shutil
import tempfile
import unittest
from pathlib import Path

import jsonschema

ROOT = Path(__file__).resolve().parents[2]
GATES = ROOT / "gates"
SSH_KEY = "ssh.agentForwardingEnabled"
ARTIFACT_SHA = "a" * 64
ARTIFACT_URL = (
    "https://github.com/docker/docker-agent/releases/download/v1.136.0/docker-agent-linux-arm64"
)
CLAUDE_DIGEST = "sha256:94670d5b2a24" + "0" * 52
CODEX_DIGEST = "sha256:6a1b26c279bb" + "0" * 52


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class G6Recorder(unittest.TestCase):
    """Each test starts from captures where every criterion passes, then breaks one."""

    @classmethod
    def setUpClass(cls):
        cls.record_module = _load("g6_record", GATES / "G6" / "record.py")
        cls.rules = _load("eligibility_rules", GATES / "eligibility_rules.py")
        cls.evidence_schema = json.loads((GATES / "evidence.schema.json").read_text(encoding="utf-8"))

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.work = self.tmp / "work"
        self.work.mkdir()
        self.versions_path = self.tmp / "versions.yaml"
        shutil.copy(ROOT / "runtime" / "versions.yaml", self.versions_path)
        self.versions_before = self.versions_path.read_text(encoding="utf-8")
        self.evidence_path = self.tmp / "G6.json"
        pins = json.loads(self.versions_before)

        probe = {
            "docker_agent_version": "v1.136.0",
            "docker_agent_sha256": ARTIFACT_SHA,
            "python3_path": "/usr/bin/python3",
            "python3_min_exit": "0",
            "python3_version": "3.14.4",
            "kit_probe": "yes",
            "kit_manifest": "yes",
            "managed_settings": "yes",
            "skill_one": "yes",
            "skill_two": "yes",
            "skills_store_mounted": "0",
            "workspace_mounted": "0",
            "host_fs_mount_targets": "/etc/resolv.conf /etc/hosts",
            "workspace_entries": "0",
            "os_release": "Ubuntu 26.04 LTS",
        }
        self.probes = {"dca-g6-claude": dict(probe), "dca-g6-codex": dict(probe)}
        self.files = {
            "pf-version.json": {
                "client": {"version": pins["sbx"]["exact"]},
                "server": {"state": "running", "version": pins["sbx"]["exact"]},
            },
            "pf-ssh.json": {"key": SSH_KEY, "value": False, "source": "override"},
            "pf-policy.json": {"rules": [self.bootstrap_rule()]},
            "ls-dca-g6-claude.json": {"sandboxes": [{"name": "dca-g6-claude", "agent": "claude"}]},
            "ls-dca-g6-codex.json": {"sandboxes": [{"name": "dca-g6-codex", "agent": "docker-agent"}]},
            "ls-after.json": {"sandboxes": []},
            "release.json": {
                "assets": [
                    {"name": "other-asset", "size": 1, "digest": "sha256:" + "b" * 64},
                    {
                        "name": "docker-agent-linux-arm64",
                        "size": 131178992,
                        "digest": f"sha256:{ARTIFACT_SHA}",
                        "browser_download_url": ARTIFACT_URL,
                    },
                ]
            },
            "templates.json": {
                "images": [
                    {"id": "94670d5b2a24", "repository": "docker.io/docker/sandbox-templates",
                     "tag": "claude-code-docker", "size": 936879795},
                    {"id": "6a1b26c279bb", "repository": "docker.io/docker/sandbox-templates",
                     "tag": "docker-agent-docker", "size": 752391782},
                ]
            },
        }
        self.obs = {
            "pf_version_exit": "0", "pf_ssh_exit": "0", "pf_policy_exit": "0", "pf_ls_exit": "0",
            "release_metadata_exit": "0",
            "artifact_url": ARTIFACT_URL,
            "artifact_sha256": ARTIFACT_SHA,
            "artifact_bytes": "131178992",
            "artifact_attestation_exit": "1",
            "template_ls_exit": "0",
            "kit_validate_exit": "0",
            "create_dca-g6-claude_exit": "0", "ls_dca-g6-claude_exit": "0",
            "probe_dca-g6-claude_exit": "0", "rm_dca-g6-claude_exit": "0",
            "create_dca-g6-codex_exit": "0", "ls_dca-g6-codex_exit": "0",
            "probe_dca-g6-codex_exit": "0", "rm_dca-g6-codex_exit": "0",
            "ls_after_exit": "0",
            "image_dca-g6-claude": "docker/sandbox-templates:claude-code-docker",
            "image_dca-g6-codex": "docker/sandbox-templates:docker-agent-docker",
            "base_digest_dca-g6-claude": CLAUDE_DIGEST,
            "base_digest_dca-g6-codex": CODEX_DIGEST,
            "workspace_line_dca-g6-claude": "none · no workspace bind mount",
            "workspace_line_dca-g6-codex": "none · no workspace bind mount",
        }

    @staticmethod
    def bootstrap_rule(**overrides):
        rule = {
            "id": "default-deny-all", "name": "default-deny-all", "policy_name": "default-deny-all",
            "scope": "global", "applies_to": "all", "resource_type": "network", "decision": "deny",
            "resources": ["**"], "origin": "local", "layer": "local", "status": "active",
            "editable": False,
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
        self.assertEqual(self.versions_path.read_text(encoding="utf-8"), self.versions_before)

    # --- the passing baseline ----------------------------------------------------------------

    def test_everything_passing_writes_the_g6_pins_and_valid_evidence(self):
        seeded = json.loads(self.versions_before)
        seeded["docker_agent_artifact"] = {"sha256": None, "url": None, "verification": None}
        seeded["sandbox_bases"] = {b: {"base": None, "version": None} for b in ("claude", "codex")}
        self.versions_path.write_text(
            json.dumps(seeded, indent=2, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8"
        )
        self.versions_before = self.versions_path.read_text(encoding="utf-8")

        status, results = self.results()
        self.assertEqual(status, "PASS", results)

        versions = json.loads(self.versions_path.read_text(encoding="utf-8"))
        self.assertEqual(
            versions["docker_agent_artifact"],
            {"sha256": ARTIFACT_SHA, "url": ARTIFACT_URL, "verification": "release-asset-digest"},
        )
        self.assertEqual(versions["sandbox_bases"]["claude"]["version"], CLAUDE_DIGEST)
        self.assertEqual(versions["sandbox_bases"]["codex"]["version"], CODEX_DIGEST)
        # G6 writes only its own pins.
        self.assertEqual(versions["sbx"], json.loads(self.versions_before)["sbx"])
        self.assertEqual(versions["claude_code"], json.loads(self.versions_before)["claude_code"])
        self.assertEqual(
            self.versions_path.read_text(encoding="utf-8"),
            json.dumps(versions, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        )

        evidence = json.loads(self.evidence_path.read_text(encoding="utf-8"))
        self.assertEqual(
            [e.message for e in jsonschema.Draft202012Validator(self.evidence_schema).iter_errors(evidence)],
            [],
        )
        self.assertEqual(evidence["provenance"], self.rules.evidence_provenance("G6", versions))
        self.assertEqual(self.rules.evidence_problems(evidence, versions), [])

    # --- release-asset digest ------------------------------------------------------------------

    def test_release_metadata_must_match_the_downloaded_artifact(self):
        asset = self.files["release.json"]["assets"][1]
        cases = {
            "digest differs": lambda: asset.update({"digest": "sha256:" + "c" * 64}),
            "digest absent": lambda: asset.pop("digest"),
            "digest not sha256": lambda: asset.update({"digest": "md5:" + "c" * 32}),
            "size differs": lambda: asset.update({"size": 42}),
            "url differs": lambda: asset.update({"browser_download_url": "https://example.invalid/x"}),
            "asset missing": lambda: self.files["release.json"]["assets"].remove(asset),
            "metadata call failed": lambda: self.obs.update({"release_metadata_exit": "1"}),
        }
        for label, break_it in cases.items():
            with self.subTest(case=label):
                self.setUp()
                asset = self.files["release.json"]["assets"][1]
                break_it()
                self.assert_fails("G6.1b")

    def test_release_asset_digest_is_not_called_a_signature(self):
        self.results()
        evidence = json.loads(self.evidence_path.read_text(encoding="utf-8"))
        observed = next(r["evidence_ref"] for r in evidence["criteria"] if r["id"] == "G6.1b")
        self.assertIn("release-hosting asset digest", observed)
        self.assertIn("not a Docker signature or attestation", observed)
        self.assertNotIn("publisher-signature", json.dumps(evidence))

    # --- base identity ---------------------------------------------------------------------------

    def test_base_requires_the_cached_template_to_match_the_registry_digest(self):
        cases = {
            "wrong cached image id": lambda: self.files["templates.json"]["images"][0].update(
                {"id": "deadbeef1234"}
            ),
            "right tag, wrong repository": lambda: self.files["templates.json"]["images"][0].update(
                {"repository": "docker.io/someone-else/sandbox-templates"}
            ),
            "right repository, wrong tag": lambda: self.files["templates.json"]["images"][0].update(
                {"tag": "codex-docker"}
            ),
            "tag belongs to the other backend's entry": lambda: self.files["templates.json"]["images"].__setitem__(
                0, {"id": "94670d5b2a24", "repository": "docker.io/docker/other", "tag": "claude-code-docker"}
            ),
            "template missing from the store": lambda: self.files["templates.json"]["images"].pop(0),
            "template store unreadable": lambda: self.files.__setitem__("templates.json", {"unexpected": []}),
            "digest not resolved": lambda: self.obs.update({"base_digest_dca-g6-claude": ""}),
            "digest malformed": lambda: self.obs.update({"base_digest_dca-g6-claude": "sha256:short"}),
            "sbx reported no image": lambda: self.obs.update({"image_dca-g6-claude": ""}),
        }
        for label, break_it in cases.items():
            with self.subTest(case=label):
                self.setUp()
                break_it()
                self.assert_fails("G6.claude.base")

    def test_template_identity_needs_reference_repository_tag_and_digest(self):
        cached = {"id": "94670d5b2a24", "repository": "docker.io/docker/sandbox-templates",
                  "tag": "claude-code-docker"}
        image = "docker/sandbox-templates:claude-code-docker"
        matches = self.record_module.template_identity_matches
        self.assertTrue(matches(cached, image, CLAUDE_DIGEST))
        self.assertFalse(matches(dict(cached, repository="docker.io/evil/sandbox-templates"), image, CLAUDE_DIGEST))
        self.assertFalse(matches(dict(cached, tag="docker-agent-docker"), image, CLAUDE_DIGEST))
        self.assertFalse(matches(dict(cached, id="6a1b26c279bb"), image, CLAUDE_DIGEST))
        self.assertFalse(matches(None, image, CLAUDE_DIGEST))
        self.assertFalse(matches(cached, image, CODEX_DIGEST))
        self.assertFalse(matches(cached, "", CLAUDE_DIGEST))

    def test_a_decoy_entry_with_the_same_tag_does_not_hide_the_real_template(self):
        # Two cached entries can share a tag across repositories. The lookup must select by
        # repository and tag, not by tag alone, or a decoy would mask the real template.
        decoy = {"id": "deadbeef1234", "repository": "docker.io/evil/sandbox-templates",
                 "tag": "claude-code-docker", "size": 1}
        self.files["templates.json"]["images"].insert(0, decoy)
        status, results = self.results()
        self.assertEqual(status, "PASS", results)
        self.assertEqual(results["G6.claude.base"], "PASS")
        self.assertEqual(
            self.record_module.template_image(
                str(self.work), "docker/sandbox-templates", "claude-code-docker"
            )["id"],
            "94670d5b2a24",
        )

    def test_evidence_does_not_call_the_short_image_id_a_digest(self):
        self.results()
        evidence = json.loads(self.evidence_path.read_text(encoding="utf-8"))
        observed = next(r["evidence_ref"] for r in evidence["criteria"] if r["id"] == "G6.claude.base")
        self.assertIn("a short image id, not a digest on its own", observed)
        self.assertIn("sbx_resolved_base", observed)
        self.assertIn("registry_digest_at_gate_time", observed)

    def test_template_store_read_is_required(self):
        self.obs["template_ls_exit"] = "1"
        self.assert_fails("G6.templates")

    # --- G0 bootstrap anti-drift (G6.0c) ----------------------------------------------------------

    def test_only_the_exact_g0_bootstrap_rule_satisfies_the_recheck(self):
        cases = {
            "sandbox-scoped": {"scope": "sandbox"},
            "applies to one sandbox": {"applies_to": "dca-g6-claude"},
            "organization layer": {"layer": "organization"},
            "remote origin": {"origin": "remote"},
            "inactive": {"status": "inactive"},
            "different id": {"id": "my-deny-all"},
            "allow not deny": {"decision": "allow"},
            "narrower resources": {"resources": ["example.com"]},
            "extra resource": {"resources": ["**", "example.com"]},
            "filesystem rule": {"resource_type": "filesystem:read"},
        }
        for label, override in cases.items():
            with self.subTest(rule=label):
                self.setUp()
                self.files["pf-policy.json"] = {"rules": [self.bootstrap_rule(**override)]}
                self.assertFalse(self.record_module.bootstrap_rule_matches(self.bootstrap_rule(**override)))
                self.assert_fails("G6.0c")

    def test_missing_fields_fail_closed(self):
        for field in ("id", "scope", "applies_to", "resource_type", "decision", "resources",
                      "origin", "layer", "status"):
            with self.subTest(missing=field):
                self.setUp()
                rule = self.bootstrap_rule()
                del rule[field]
                self.files["pf-policy.json"] = {"rules": [rule]}
                self.assert_fails("G6.0c")

    def test_the_exact_bootstrap_rule_passes_and_a_second_deny_all_does_not(self):
        self.assertTrue(self.record_module.bootstrap_rule_matches(self.bootstrap_rule()))
        self.files["pf-policy.json"] = {"rules": [self.bootstrap_rule(), self.bootstrap_rule()]}
        self.assert_fails("G6.0c")

    def test_the_bootstrap_rule_must_be_the_only_network_rule(self):
        # G6.0c claims the network state is still the post-G0 baseline, so any further network
        # rule is drift, whatever its scope or origin. Filesystem rules are separate.
        filesystem = {
            "id": "default-fs-read-allow-all", "scope": "global", "applies_to": "all",
            "resource_type": "filesystem:read", "decision": "allow", "resources": ["**"],
            "origin": "local", "layer": "local", "status": "active",
        }
        with self.subTest(case="exact bootstrap only, alongside filesystem rules"):
            self.files["pf-policy.json"] = {
                "rules": [filesystem, self.bootstrap_rule(), dict(filesystem, resource_type="filesystem:write")]
            }
            status, results = self.results()
            self.assertEqual(results["G6.0c"], "PASS", results)
            self.assertEqual(status, "PASS")

        extras = {
            "global allow": self.bootstrap_rule(
                id="allow-registry", decision="allow", resources=["registry.example.com"]
            ),
            "sandbox network rule": self.bootstrap_rule(
                id="sandbox-allow", scope="sandbox", applies_to="dca-g6-claude",
                decision="allow", resources=["example.com"]
            ),
            "organization network rule": self.bootstrap_rule(
                id="org-allow", layer="organization", origin="remote",
                decision="allow", resources=["example.com"]
            ),
            "second unrelated deny": self.bootstrap_rule(
                id="deny-metadata", decision="deny", resources=["169.254.169.254"]
            ),
        }
        for label, extra in extras.items():
            with self.subTest(case=f"bootstrap plus {label}"):
                self.setUp()
                self.files["pf-policy.json"] = {"rules": [filesystem, self.bootstrap_rule(), extra]}
                self.assert_fails("G6.0c")

    def test_preflight_guard_blocks_everything_after_it(self):
        self.files["pf-ssh.json"] = {"key": SSH_KEY, "value": True, "source": "default"}
        status, results = self.results()
        self.assertEqual(status, "FAIL")
        self.assertEqual(results["G6.0b"], "FAIL")
        for later in ("G6.1", "G6.2", "G6.claude.create", "G6.pins"):
            self.assertEqual(results[later], "NOT-RUN")
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(
                self.record_module.main(["record.py", "--preflight", str(self.write()), str(self.work)]), 1
            )

    # --- sandbox evidence --------------------------------------------------------------------------

    def test_a_workspace_mount_or_shared_skills_mount_fails(self):
        cases = {
            "workspace mounted": {"workspace_mounted": "1"},
            "workspace not empty": {"workspace_entries": "3"},
            "shared skills mounted": {"skills_store_mounted": "1"},
        }
        for label, change in cases.items():
            with self.subTest(case=label):
                self.setUp()
                self.probes["dca-g6-claude"].update(change)
                self.assert_fails("G6.claude.create")

    def test_sbx_must_report_no_workspace_bind_mount(self):
        self.obs["workspace_line_dca-g6-claude"] = "/Users/dev/repo · bind mount"
        self.assert_fails("G6.claude.create")

    def test_kit_evidence_requires_the_artifact_hash_to_match_in_the_vm(self):
        self.probes["dca-g6-codex"]["docker_agent_sha256"] = "d" * 64
        self.assert_fails("G6.codex.kit")

    def test_python_below_the_minimum_fails(self):
        self.probes["dca-g6-codex"].update({"python3_version": "3.10.14", "python3_min_exit": "1"})
        self.assert_fails("G6.codex.python")

    def test_a_remaining_sandbox_or_failed_removal_fails(self):
        self.setUp()
        self.obs["rm_dca-g6-claude_exit"] = "1"
        self.assert_fails("G6.claude.cleanup")
        self.setUp()
        self.files["ls-after.json"] = {"sandboxes": [{"name": "dca-g6-codex"}]}
        self.assert_fails("G6.clean")

    def test_run_sh_cleans_up_before_writing_fail_evidence(self):
        script = (GATES / "G6" / "run.sh").read_text(encoding="utf-8")
        body = "\n".join(line for line in script.splitlines() if not line.lstrip().startswith("#"))
        stop = body.index("stop() {")
        stop_end = body.index("}", body.index("record.py", stop))
        stop_body = body[stop:stop_end]
        self.assertLess(stop_body.index("cleanup"), stop_body.index("record.py"))
        self.assertIn("trap cleanup EXIT INT TERM", body)
        self.assertNotIn("--all", body)
        self.assertIn("sbx rm --force", body)


if __name__ == "__main__":
    unittest.main()
