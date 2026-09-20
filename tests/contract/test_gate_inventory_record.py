"""Contract tests for the control-plane inventory recorder (tasks.md T014, gates/inventory/record.py).

They run the decision logic against synthetic captures in a temporary work directory: no sandbox,
no sbx command, no change to the real runtime/versions.yaml or to the committed inventory files.
The shapes mirror what the sbx version pinned by G0 emits on this host.

The property that carries T014 is the bidirectional kit cross-check: what the inventory attributes
to a built-in kit must be exactly what the kit was observed declaring. A fabricated host and a
silently dropped one must both fail, so each is mutated here and asserted to fail. The committed
inventory is also run through every checker unchanged, so a later edit that breaks a rule is
caught by the suite rather than at the next gate.
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
INVENTORY = GATES / "inventory"

CLAUDE_KIT_HOSTS = [
    "api.anthropic.com:443",
    "platform.claude.com:443",
    "downloads.claude.ai:443",
    "claude.com:443",
    "code.claude.com:443",
    "mcp-proxy.anthropic.com:443",
    "bridge.claudeusercontent.com:443",
]


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def entry(host, purpose="discovery", required=False, profiles=None, source="kit", port=443):
    return {
        "host": host,
        "port": port,
        "purpose": purpose,
        "sandbox_required": required,
        "profiles": list(profiles or []),
        "source": source,
        "evidence_ref": f"gates/inventory/work/policy.json rule kit:{host}",
    }


def inventory_of(claude_hosts, codex_hosts):
    return {
        "backends": {
            "claude": {"hosts": list(claude_hosts)},
            "codex": {"hosts": list(codex_hosts)},
        }
    }


def policy_document(sandbox, resources, decision="allow", scope=None):
    return {
        "rules": [
            {
                "id": "default-fs-read-allow-all",
                "scope": "global",
                "resource_type": "filesystem:read",
                "decision": "allow",
                "resources": ["**"],
            },
            {
                "id": "e9ab",
                "name": f"kit:{sandbox}",
                "scope": scope if scope is not None else f"sandbox:{sandbox}",
                "applies_to": f"sandbox:{sandbox}",
                "resource_type": "network",
                "decision": decision,
                "resources": list(resources),
                "origin": "scoped",
                "layer": "local",
                "status": "active",
            },
        ]
    }


class InventoryRecorder(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.record_module = _load("inventory_record", INVENTORY / "record.py")
        cls.rules = _load("eligibility_rules", GATES / "eligibility_rules.py")
        cls.evidence_schema = json.loads((GATES / "evidence.schema.json").read_text(encoding="utf-8"))
        cls.real_inventory = json.loads((INVENTORY / "control-plane-hosts.json").read_text(encoding="utf-8"))
        cls.real_draft = json.loads((INVENTORY / "trusted-allowlist.draft.json").read_text(encoding="utf-8"))

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.work = self.tmp / "work"
        self.work.mkdir()
        self.pins = json.loads((ROOT / "runtime" / "versions.yaml").read_text(encoding="utf-8"))
        self._write_work()

    # --- what the kit was observed declaring ---------------------------------------------------

    def test_01_kit_allowances_read_the_sandbox_scoped_allow_rule(self):
        document = policy_document("dca-inv-claude", CLAUDE_KIT_HOSTS)
        self.assertEqual(
            self.record_module.kit_network_allowances(document, "dca-inv-claude"),
            set(CLAUDE_KIT_HOSTS),
        )

    def test_02_no_network_rule_is_an_empty_set_not_an_unreadable_one(self):
        """A kit that declares nothing and a listing that could not be read must stay distinct."""
        document = {"rules": [{"id": "fs", "scope": "global", "resource_type": "filesystem:read",
                               "decision": "allow", "resources": ["**"]}]}
        self.assertEqual(self.record_module.kit_network_allowances(document, "dca-inv-codex"), set())
        self.assertIsNone(self.record_module.kit_network_allowances(None, "dca-inv-codex"))
        self.assertIsNone(self.record_module.kit_network_allowances({"rules": "nonsense"}, "dca-inv-codex"))

    def test_03_another_sandboxes_rule_is_not_this_sandboxes_declaration(self):
        document = policy_document("dca-inv-claude", CLAUDE_KIT_HOSTS)
        self.assertEqual(self.record_module.kit_network_allowances(document, "dca-inv-codex"), set())

    def test_04_a_global_or_deny_rule_is_not_a_kit_allowance(self):
        globally = policy_document("dca-inv-claude", ["api.anthropic.com:443"], scope="global")
        self.assertEqual(self.record_module.kit_network_allowances(globally, "dca-inv-claude"), set())
        denied = policy_document("dca-inv-claude", ["api.anthropic.com:443"], decision="deny")
        self.assertEqual(self.record_module.kit_network_allowances(denied, "dca-inv-claude"), set())

    def test_05_unparsable_resources_fail_closed(self):
        document = policy_document("dca-inv-claude", ["api.anthropic.com:443"])
        document["rules"][1]["resources"] = {"host": "api.anthropic.com"}
        self.assertIsNone(self.record_module.kit_network_allowances(document, "dca-inv-claude"))

    # --- the bidirectional cross-check ----------------------------------------------------------

    def test_06_crosscheck_passes_when_kit_hosts_match_exactly(self):
        inventory = inventory_of([entry(h.split(":")[0]) for h in CLAUDE_KIT_HOSTS], [])
        observed = {"claude": set(CLAUDE_KIT_HOSTS), "codex": set()}
        self.assertEqual(self.record_module.kit_crosscheck_problems(inventory, observed), [])

    def test_07_a_fabricated_kit_host_fails(self):
        hosts = [entry(h.split(":")[0]) for h in CLAUDE_KIT_HOSTS]
        hosts.append(entry("telemetry.example.com"))
        inventory = inventory_of(hosts, [])
        problems = self.record_module.kit_crosscheck_problems(
            inventory, {"claude": set(CLAUDE_KIT_HOSTS), "codex": set()}
        )
        self.assertTrue(any("telemetry.example.com:443" in p and "no such allowance" in p for p in problems),
                        problems)

    def test_08_a_silently_dropped_kit_host_fails(self):
        hosts = [entry(h.split(":")[0]) for h in CLAUDE_KIT_HOSTS[1:]]
        inventory = inventory_of(hosts, [])
        problems = self.record_module.kit_crosscheck_problems(
            inventory, {"claude": set(CLAUDE_KIT_HOSTS), "codex": set()}
        )
        self.assertTrue(any("api.anthropic.com:443" in p and "does not record it" in p for p in problems),
                        problems)

    def test_09_a_kit_host_relabelled_as_docs_no_longer_matches_the_kit(self):
        """Attributing an observed kit host to documentation would hide where it came from."""
        hosts = [entry(h.split(":")[0]) for h in CLAUDE_KIT_HOSTS[1:]]
        hosts.append(entry("api.anthropic.com", source="docs"))
        problems = self.record_module.kit_crosscheck_problems(
            inventory_of(hosts, []), {"claude": set(CLAUDE_KIT_HOSTS), "codex": set()}
        )
        self.assertTrue(any("api.anthropic.com:443" in p for p in problems), problems)

    def test_10_an_unreadable_observation_fails_closed(self):
        inventory = inventory_of([entry(h.split(":")[0]) for h in CLAUDE_KIT_HOSTS], [])
        problems = self.record_module.kit_crosscheck_problems(
            inventory, {"claude": None, "codex": set()}
        )
        self.assertTrue(any("unproven" in p for p in problems), problems)

    # --- structure ------------------------------------------------------------------------------

    def test_11_the_committed_inventory_is_well_formed(self):
        self.assertEqual(self.record_module.structure_problems(self.real_inventory), [])
        self.assertEqual(self.record_module.classification_problems(self.real_inventory), [])
        self.assertEqual(self.record_module.control_plane_problems(self.real_inventory), [])
        self.assertEqual(self.record_module.secret_problems(self.real_inventory), [])
        self.assertEqual(self.record_module.allowlist_problems(self.real_draft), [])

    def test_12_a_missing_required_field_fails(self):
        for field in ("host", "port", "purpose", "sandbox_required", "profiles", "source", "evidence_ref"):
            with self.subTest(field=field):
                bad = entry("api.anthropic.com")
                del bad[field]
                problems = self.record_module.structure_problems(inventory_of([bad], []))
                self.assertTrue(problems, f"a missing {field} was accepted")

    def test_13_unknown_values_and_fields_fail(self):
        cases = {
            "purpose": {"purpose": "telemetry"},
            "source": {"source": "guess"},
            "profiles": {"profiles": ["production"]},
            "port": {"port": "443"},
            "sandbox_required": {"sandbox_required": "yes"},
            "extra": {"allow": True},
            "host": {"host": "https://api.anthropic.com/v1"},
            "path": {"path": "backend-api"},
        }
        for name, patch in cases.items():
            with self.subTest(case=name):
                bad = entry("api.anthropic.com")
                bad.update(patch)
                self.assertTrue(self.record_module.structure_problems(inventory_of([bad], [])),
                                f"{name} was accepted")

    def test_14_a_duplicate_host_fails_without_reconciliation(self):
        duplicated = [entry("api.anthropic.com"), entry("api.anthropic.com", purpose="runtime-control-plane")]
        problems = self.record_module.structure_problems(inventory_of(duplicated, []))
        self.assertTrue(any("appears twice" in p for p in problems), problems)

    def test_15_an_empty_evidence_ref_fails(self):
        bad = entry("api.anthropic.com")
        bad["evidence_ref"] = "   "
        problems = self.record_module.structure_problems(inventory_of([bad], []))
        self.assertTrue(any("evidence_ref" in p for p in problems), problems)

    def test_16_a_missing_backend_section_fails(self):
        self.assertTrue(self.record_module.structure_problems({"backends": {"claude": {"hosts": []}}}))
        self.assertTrue(self.record_module.structure_problems({}))

    # --- classification ---------------------------------------------------------------------------

    def test_17_sandbox_required_needs_a_runtime_purpose(self):
        for purpose in ("host-oauth-login", "discovery"):
            with self.subTest(purpose=purpose):
                bad = entry("claude.com", purpose=purpose, required=True, profiles=["trusted"])
                problems = self.record_module.classification_problems(inventory_of([bad], []))
                self.assertTrue(problems, f"{purpose} was accepted as sandbox_required")

    def test_18_sandbox_required_without_a_profile_fails(self):
        bad = entry("api.anthropic.com", purpose="runtime-control-plane", required=True, profiles=[])
        problems = self.record_module.classification_problems(inventory_of([bad], []))
        self.assertTrue(any("names no profile" in p for p in problems), problems)

    def test_19_profiles_on_a_host_the_sandbox_does_not_need_fail(self):
        bad = entry("claude.com", purpose="host-oauth-login", required=False, profiles=["trusted"])
        problems = self.record_module.classification_problems(inventory_of([bad], []))
        self.assertTrue(any("not sandbox_required but lists profiles" in p for p in problems), problems)

    def test_20_a_refresh_host_may_be_required_for_one_profile(self):
        """The promotion path stays open: refresh is a sandbox purpose, scoped to its profile."""
        ok = entry("auth.example.com", purpose="refresh", required=True, profiles=["trusted"], source="docs")
        self.assertEqual(self.record_module.classification_problems(inventory_of([], [ok])), [])

    def test_21_auth_openai_com_cannot_be_promoted_at_this_stage(self):
        promoted = entry("auth.openai.com", purpose="refresh", required=True, profiles=["trusted"], source="docs")
        problems = self.record_module.classification_problems(inventory_of([], [promoted]))
        self.assertTrue(any("must stay 'host-oauth-login'" in p for p in problems), problems)
        self.assertTrue(any("without gate evidence" in p for p in problems), problems)

    def test_22_auth_openai_com_required_under_its_own_purpose_still_fails(self):
        bad = entry("auth.openai.com", purpose="host-oauth-login", required=True, profiles=["trusted"], source="docs")
        self.assertTrue(self.record_module.classification_problems(inventory_of([], [bad])))

    def test_23_a_backend_without_a_required_runtime_control_plane_fails(self):
        hosts = [entry("api.anthropic.com", purpose="runtime-control-plane", required=True, profiles=["trusted"])]
        problems = self.record_module.control_plane_problems(inventory_of(hosts, []))
        self.assertTrue(any(p.startswith("codex:") for p in problems), problems)
        self.assertFalse(any(p.startswith("claude:") for p in problems), problems)

    def test_24_a_documented_runtime_host_that_is_not_required_does_not_count(self):
        hosts = [entry("chatgpt.com", purpose="runtime-control-plane", required=False, source="docs")]
        problems = self.record_module.control_plane_problems(inventory_of(hosts, hosts))
        self.assertEqual(len(problems), 2, problems)

    # --- secrets ------------------------------------------------------------------------------------

    def test_25_a_secret_shaped_field_name_fails(self):
        for key in ("token", "api_key", "authorization", "cookie", "password", "bearer"):
            with self.subTest(key=key):
                bad = entry("api.anthropic.com")
                bad[key] = "x"
                self.assertTrue(self.record_module.secret_problems(inventory_of([bad], [])),
                                f"{key} was accepted")

    def test_26_a_credential_shaped_value_fails_but_a_hash_does_not(self):
        opaque = entry("api.anthropic.com")
        opaque["note"] = "sk-ant-oat01-AbCdEfGhIjKlMnOpQrStUvWxYz0123456789"
        self.assertTrue(self.record_module.secret_problems(inventory_of([opaque], [])))

        hashed = entry("api.anthropic.com")
        hashed["note"] = "a0c95df654a017ad67adc2bb9f0d6e90f7b64ca48faf9be55ac711dea7491623"
        self.assertEqual(self.record_module.secret_problems(inventory_of([hashed], [])), [])

    # --- the trusted allowlist draft ------------------------------------------------------------------

    def test_27_an_approved_draft_fails(self):
        draft = json.loads(json.dumps(self.real_draft))
        draft["approved"] = True
        problems = self.record_module.allowlist_problems(draft)
        self.assertTrue(any("approved=false" in p for p in problems), problems)

    def test_28_an_approved_candidate_fails(self):
        draft = json.loads(json.dumps(self.real_draft))
        draft["candidates"][0]["approved"] = True
        problems = self.record_module.allowlist_problems(draft)
        self.assertTrue(any("must record approved=false" in p for p in problems), problems)

    def test_29_a_candidate_outside_the_declared_categories_fails(self):
        draft = json.loads(json.dumps(self.real_draft))
        draft["candidates"][0]["category"] = "deployment"
        self.assertTrue(any("not a declared R12 category" in p
                            for p in self.record_module.allowlist_problems(draft)))

    def test_30_the_draft_can_never_widen_untrusted_access(self):
        draft = json.loads(json.dumps(self.real_draft))
        draft["applies_to_profiles"] = ["trusted", "untrusted"]
        self.assertTrue(any("trusted profile only" in p
                            for p in self.record_module.allowlist_problems(draft)))

    def test_31_a_candidate_claiming_to_be_sandbox_required_fails(self):
        draft = json.loads(json.dumps(self.real_draft))
        draft["candidates"][0]["sandbox_required"] = True
        self.assertTrue(any("not a control-plane requirement" in p
                            for p in self.record_module.allowlist_problems(draft)))

    def test_32_a_duplicated_or_malformed_candidate_fails(self):
        draft = json.loads(json.dumps(self.real_draft))
        draft["candidates"].append(dict(draft["candidates"][0]))
        self.assertTrue(any("twice" in p for p in self.record_module.allowlist_problems(draft)))

        draft = json.loads(json.dumps(self.real_draft))
        draft["candidates"][0]["rationale"] = ""
        self.assertTrue(any("no rationale" in p for p in self.record_module.allowlist_problems(draft)))

    # --- the recorder end to end ----------------------------------------------------------------------

    def _write_work(self):
        """The captured files of a clean passing run.

        Called once from setUp so a test can mutate a file afterwards and have the mutation
        survive: building the observations must never rewrite the capture underneath it.
        """
        (self.work / "pf-version.json").write_text(json.dumps({
            "client": {"version": self.pins["sbx"]["exact"]},
            "server": {"version": self.pins["sbx"]["exact"], "state": "running"},
        }), encoding="utf-8")
        (self.work / "pf-ssh-forwarding.json").write_text(
            json.dumps({"key": "ssh.agentForwardingEnabled", "value": False}), encoding="utf-8")
        (self.work / "pf-ssh-socket.json").write_text(
            json.dumps({"key": "ssh.agentSocketPath", "value": ""}), encoding="utf-8")
        baseline = {"rules": [dict(_load("dca_preflight", GATES / "preflight.py").BOOTSTRAP_RULE)]}
        (self.work / "pf-policy.json").write_text(json.dumps(baseline), encoding="utf-8")
        (self.work / "policy-after.json").write_text(json.dumps(baseline), encoding="utf-8")
        (self.work / "pf-ls.json").write_text(json.dumps({"sandboxes": []}), encoding="utf-8")
        (self.work / "ls-after.json").write_text(json.dumps({"sandboxes": []}), encoding="utf-8")
        (self.work / "policy-claude.json").write_text(
            json.dumps(policy_document("dca-inv-claude", CLAUDE_KIT_HOSTS)), encoding="utf-8")
        (self.work / "policy-codex.json").write_text(
            json.dumps({"rules": []}), encoding="utf-8")

    def _capture(self, **overrides):
        """The observations of a clean passing run, which each test then mutates."""
        obs = {
            "sbx_env_ssh_auth_sock": "removed",
            "pf_version_exit": "0",
            "pf_ssh_forwarding_exit": "0",
            "pf_ssh_socket_exit": "0",
            "pf_policy_exit": "0",
            "pf_ls_exit": "0",
            "kit_present": "yes",
            "artifact_sha256": self.pins["docker_agent_artifact"]["sha256"],
            "artifact_runtime_url": "yes",
            "artifact_login_url": "yes",
            "create_dca-inv-claude_exit": "0",
            "policy_dca-inv-claude_exit": "0",
            "rm_dca-inv-claude_exit": "0",
            "create_dca-inv-codex_exit": "0",
            "policy_dca-inv-codex_exit": "0",
            "rm_dca-inv-codex_exit": "0",
            "ls_after_exit": "0",
            "policy_after_exit": "0",
        }
        obs.update(overrides)
        return obs

    def _results(self, obs):
        criteria = self.record_module.evaluate(
            obs, str(self.work),
            hosts_file=str(INVENTORY / "control-plane-hosts.json"),
            allowlist_file=str(INVENTORY / "trusted-allowlist.draft.json"),
        )
        return {row["id"]: row["result"] for row in criteria}

    def test_33_the_recorded_capture_passes_every_criterion(self):
        results = self._results(self._capture())
        self.assertTrue(results, "no criteria were produced")
        self.assertEqual({r for r in results.values()}, {"PASS"}, results)

    def test_34_a_failed_preflight_leaves_later_criteria_not_run(self):
        """A drifted baseline must not be reported as a passing inventory."""
        (self.work / "pf-ls.json").write_text(
            json.dumps({"sandboxes": [{"name": "leftover"}]}), encoding="utf-8")
        results = self._results(self._capture())
        self.assertEqual(results["INV.0d"], "FAIL")
        self.assertEqual(results["INV.4"], "NOT-RUN")
        self.assertEqual(results["INV.5"], "NOT-RUN")

    def test_35_an_unread_kit_declaration_is_not_a_passing_crosscheck(self):
        results = self._results(self._capture(**{"policy_dca-inv-claude_exit": "1"}))
        self.assertEqual(results["INV.1.claude"], "FAIL")
        self.assertEqual(results["INV.4"], "NOT-RUN")

    def test_36_a_missing_codex_endpoint_in_the_pinned_artifact_fails(self):
        results = self._results(self._capture(artifact_runtime_url="no"))
        self.assertEqual(results["INV.6"], "FAIL")

    def test_37_a_leftover_sandbox_or_a_widened_global_policy_fails(self):
        (self.work / "ls-after.json").write_text(
            json.dumps({"sandboxes": [{"name": "dca-inv-claude"}]}), encoding="utf-8")
        self.assertEqual(self._results(self._capture())["INV.clean"], "FAIL")

        self.setUp()
        obs = self._capture()
        widened = json.loads((self.work / "policy-after.json").read_text(encoding="utf-8"))
        widened["rules"].append({
            "id": "added", "scope": "global", "applies_to": "all", "resource_type": "network",
            "decision": "allow", "resources": ["api.anthropic.com:443"], "origin": "local",
            "layer": "local", "status": "active",
        })
        (self.work / "policy-after.json").write_text(json.dumps(widened), encoding="utf-8")
        self.assertEqual(self._results(obs)["INV.baseline"], "FAIL")

    def test_38_a_failed_removal_fails_cleanup(self):
        results = self._results(self._capture(**{"rm_dca-inv-codex_exit": "1"}))
        self.assertEqual(results["INV.clean"], "FAIL")

    # --- the evidence document -------------------------------------------------------------------

    def test_39_the_written_evidence_matches_the_contract_and_is_current(self):
        obs_path = self.tmp / "observations.env"
        obs_path.write_text(
            "".join(f"{k}={v}\n" for k, v in self._capture().items()), encoding="utf-8")
        evidence_path = self.tmp / "INVENTORY.json"
        status, _ = self.record_module.record(
            str(obs_path), str(self.work), str(ROOT / "runtime" / "versions.yaml"), str(evidence_path))
        self.assertEqual(status, "PASS")

        evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
        jsonschema.Draft202012Validator(self.evidence_schema).validate(evidence)
        self.assertEqual(evidence["gate"], "INVENTORY")
        self.assertEqual(self.rules.evidence_problems(evidence, self.pins), [])

    def test_40_the_committed_evidence_is_current_and_passing(self):
        evidence = json.loads((GATES / "INVENTORY.json").read_text(encoding="utf-8"))
        jsonschema.Draft202012Validator(self.evidence_schema).validate(evidence)
        self.assertEqual(evidence["gate"], "INVENTORY")
        self.assertEqual(evidence["status"], "PASS")
        self.assertEqual(self.rules.evidence_problems(evidence, self.pins), [])
        self.assertTrue(all(row["result"] == "PASS" for row in evidence["criteria"]), evidence["criteria"])


if __name__ == "__main__":
    unittest.main()
