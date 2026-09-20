"""Contract tests for the G4 recorder (tasks.md T015, gates/G4/record.py).

They run the decision logic against synthetic captures in a temporary work directory: no sandbox,
no sbx command, no change to the real runtime/versions.yaml or to the committed T014 documents. The
shapes mirror what the sbx version pinned by G0 emits on this host.

G4's claim is not "each destination behaved" but "the effective policy is exactly this", so the
tests push on the ways that claim could be weaker than it looks. The sharpest is the latent allow:
a rule permitting a destination no probe exercised, which the check, the connection attempt and the
log are all structurally blind to. Only inspecting the effective rule set catches it, so that
surface gets its own failure cases here.
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
INVENTORY_FILE = GATES / "inventory" / "control-plane-hosts.json"
DRAFT_FILE = GATES / "inventory" / "trusted-allowlist.draft.json"

ESTABLISHED = "HTTP/1.0 200 Connection established\n\nHTTP/2 404 \ndate: Sun, 20 Sep 2026\n"
BLOCKED = "HTTP/1.0 200 OK\n\nHTTP/1.1 403 Forbidden\nContent-Length: 46\n"

CLAUDE_KIT_HOSTS = (
    "api.anthropic.com", "platform.claude.com", "downloads.claude.ai", "claude.com",
    "code.claude.com", "mcp-proxy.anthropic.com", "bridge.claudeusercontent.com",
)


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def check_doc(allowed, deny_kind=None):
    doc = {
        "action": "net:connect:tcp",
        "allowed": allowed,
        "context": "sandbox:x",
        "governance": {"active": False},
        "resource_type": "net:domain",
        "type": "network",
    }
    if not allowed:
        doc["deny_kind"] = deny_kind
        doc["reason"] = ("Denied by local rule" if deny_kind == "explicit"
                         else "No matching allow rule (default deny)")
    return doc


def governance_doc(active=False, target="example.invalid:443", allowed=False):
    """A decided `sbx policy check network --json` document, the shape pinned sbx v0.43.0 emits."""
    return {
        "action": "net:connect:tcp",
        "allowed": allowed,
        "context": "global",
        "deny_kind": "implicit",
        "governance": {"active": active},
        "reason": "No matching allow rule (default deny)",
        "resource_type": "net:domain",
        "resource_value": target,
        "target": target,
        "type": "network",
    }


def net_rule(sandbox, decision, resources, kit=False, rule_id=None, scope=None, status="active"):
    """One network rule in the shape pinned sbx v0.43.0 emits."""
    identifier = rule_id or f"{decision}-{'-'.join(resources)}"
    return {
        "id": identifier,
        "name": f"kit:{sandbox}" if kit else identifier,
        "scope": scope or f"sandbox:{sandbox}",
        "applies_to": f"sandbox:{sandbox}",
        "resource_type": "network",
        "decision": decision,
        "resources": list(resources),
        "origin": "scoped",
        "layer": "local",
        "status": status,
        "editable": not kit,
        "sandbox_id": sandbox,
        "policy_id": "p1",
    }


FS_RULES = [
    {"id": "default-fs-read-allow-all", "name": "default-fs-read-allow-all", "scope": "global",
     "applies_to": "all", "resource_type": "filesystem:read", "decision": "allow",
     "resources": ["**"], "origin": "local", "layer": "local", "status": "active",
     "editable": False, "policy_id": "local-policy"},
]


class G4Recorder(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.record_module = _load("g4_record", GATES / "G4" / "record.py")
        cls.rules = _load("eligibility_rules", GATES / "eligibility_rules.py")
        cls.preflight_module = _load("dca_preflight", GATES / "preflight.py")
        cls.evidence_schema = json.loads((GATES / "evidence.schema.json").read_text(encoding="utf-8"))
        cls.inventory = json.loads(INVENTORY_FILE.read_text(encoding="utf-8"))
        cls.draft = json.loads(DRAFT_FILE.read_text(encoding="utf-8"))
        cls.matrix = cls.record_module.cells(cls.inventory, cls.draft)
        cls.candidates = cls.record_module.trusted_candidates(cls.draft)

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.work = self.tmp / "work"
        self.work.mkdir()
        self.pins = json.loads((ROOT / "runtime" / "versions.yaml").read_text(encoding="utf-8"))
        self._write_work()

    def _governance(self, which, document):
        """Overwrite one governance probe capture; document=None removes it."""
        path = self.work / f"governance-{which}.json"
        if document is None:
            path.unlink()
        else:
            path.write_text(document if isinstance(document, str) else json.dumps(document),
                            encoding="utf-8")

    def cell(self, backend, profile):
        return next(c for c in self.matrix
                    if c["backend"] == backend and c["profile"] == profile)

    # --- the matrix derives from T014 ------------------------------------------------------------

    def test_01_four_cells_cover_both_backends_and_both_profiles(self):
        self.assertEqual(
            {(c["backend"], c["profile"]) for c in self.matrix},
            {("claude", "trusted"), ("claude", "untrusted"),
             ("codex", "trusted"), ("codex", "untrusted")},
        )

    def test_02_required_hosts_come_from_the_inventory(self):
        for cell in self.matrix:
            with self.subTest(cell=cell["name"]):
                required = "api.anthropic.com" if cell["backend"] == "claude" else "chatgpt.com"
                self.assertEqual(cell["expected"][required], self.record_module.ALLOW)

    def test_03_the_other_backends_control_plane_is_denied(self):
        for cell in self.matrix:
            other = "chatgpt.com" if cell["backend"] == "claude" else "api.anthropic.com"
            with self.subTest(cell=cell["name"]):
                self.assertNotEqual(cell["expected"][other], self.record_module.ALLOW)

    def test_04_every_t014_trusted_candidate_is_exercised_in_the_trusted_cells(self):
        """The approved architecture is the whole draft, not a convenient subset of it."""
        self.assertEqual(len(self.candidates), 11)
        for backend in ("claude", "codex"):
            cell = self.cell(backend, "trusted")
            with self.subTest(backend=backend):
                for host in self.candidates:
                    self.assertEqual(cell["expected"].get(host), self.record_module.ALLOW,
                                     f"{host} is a T014 candidate but not permitted in {cell['name']}")

    def test_05_dropping_a_candidate_from_the_draft_changes_the_matrix(self):
        draft = json.loads(json.dumps(self.draft))
        draft["candidates"] = [c for c in draft["candidates"] if c["host"] != "crates.io"]
        cell = next(c for c in self.record_module.cells(self.inventory, draft)
                    if c["name"] == "dca-g4-claude-trusted")
        self.assertNotEqual(cell["expected"].get("crates.io"), self.record_module.ALLOW)
        self.assertEqual(self.cell("claude", "trusted")["expected"]["crates.io"],
                         self.record_module.ALLOW)

    def test_06_untrusted_never_inherits_the_trusted_allowlist(self):
        for backend in ("claude", "codex"):
            cell = self.cell(backend, "untrusted")
            permitted = {h for h, d in cell["expected"].items() if d == self.record_module.ALLOW}
            with self.subTest(backend=backend):
                self.assertEqual(len(permitted), 2, permitted)
                self.assertIn(self.record_module.UNTRUSTED_GRANT, permitted)
                for host in self.candidates:
                    if host != self.record_module.UNTRUSTED_GRANT:
                        self.assertNotEqual(cell["expected"].get(host), self.record_module.ALLOW)

    def test_07_source_control_hosts_are_trusted_only(self):
        for host in ("github.com", "codeload.github.com", "objects.githubusercontent.com"):
            with self.subTest(host=host):
                self.assertEqual(self.cell("claude", "trusted")["expected"][host],
                                 self.record_module.ALLOW)
                self.assertNotEqual(self.cell("claude", "untrusted")["expected"][host],
                                    self.record_module.ALLOW)

    def test_08_untrusted_is_a_strict_subset_of_trusted(self):
        for backend in ("claude", "codex"):
            trusted = {h for h, d in self.cell(backend, "trusted")["expected"].items()
                       if d == self.record_module.ALLOW}
            untrusted = {h for h, d in self.cell(backend, "untrusted")["expected"].items()
                         if d == self.record_module.ALLOW}
            with self.subTest(backend=backend):
                self.assertTrue(untrusted < trusted, f"{untrusted} vs {trusted}")

    def test_09_claude_kit_overrides_are_computed_per_profile(self):
        """code.claude.com is a documentation candidate, so trusted permits it, untrusted denies it."""
        trusted = self.cell("claude", "trusted")
        untrusted = self.cell("claude", "untrusted")
        self.assertNotIn("code.claude.com", trusted["deny_rules"])
        self.assertEqual(trusted["expected"]["code.claude.com"], self.record_module.ALLOW)
        self.assertIn("code.claude.com", untrusted["deny_rules"])
        self.assertEqual(untrusted["expected"]["code.claude.com"],
                         self.record_module.DENY_EXPLICIT)
        self.assertEqual(set(trusted["deny_rules"]),
                         set(CLAUDE_KIT_HOSTS) - {"api.anthropic.com", "code.claude.com"})
        self.assertEqual(set(untrusted["deny_rules"]),
                         set(CLAUDE_KIT_HOSTS) - {"api.anthropic.com"})

    def test_10_codex_adds_chatgpt_com_and_needs_no_override(self):
        for profile in ("trusted", "untrusted"):
            cell = self.cell("codex", profile)
            with self.subTest(profile=profile):
                self.assertIn("chatgpt.com", cell["allow_rules"])
                self.assertEqual(cell["deny_rules"], [])

    def test_11_the_promotion_hosts_are_denied_in_every_cell(self):
        for cell in self.matrix:
            for host in ("auth.openai.com", "platform.claude.com"):
                with self.subTest(cell=cell["name"], host=host):
                    self.assertNotEqual(cell["expected"].get(host), self.record_module.ALLOW)

    def test_12_a_weakened_inventory_stops_the_matrix(self):
        stripped = json.loads(json.dumps(self.inventory))
        for host in stripped["backends"]["codex"]["hosts"]:
            host["sandbox_required"] = False
        with self.assertRaises(ValueError):
            self.record_module.cells(stripped, self.draft)
        with self.assertRaises(ValueError):
            self.record_module.cells({"backends": {"claude": {"hosts": []}, "codex": {"hosts": []}}},
                                     self.draft)

    def test_13_a_draft_entry_is_validated_not_silently_approved(self):
        for mutate, why in (
            (lambda d: d["candidates"][0].update({"category": "deployment"}), "undeclared category"),
            (lambda d: d["candidates"][0].update({"approved": True}), "approved candidate"),
            (lambda d: d["candidates"][0].update({"sandbox_required": True}), "claims required"),
            (lambda d: d["candidates"][0].update({"host": "Not A Host"}), "malformed host"),
            (lambda d: d["candidates"][0].update({"port": 80}), "wrong port"),
            (lambda d: d["candidates"][0].update({"evidence_ref": "  "}), "no evidence_ref"),
            (lambda d: d.update({"approved": True}), "approved draft"),
            (lambda d: d.update({"applies_to_profiles": ["trusted", "untrusted"]}), "widened"),
            (lambda d: d["categories"].update({"deployment": "x"}), "category outside R12"),
        ):
            draft = json.loads(json.dumps(self.draft))
            mutate(draft)
            with self.subTest(why=why):
                with self.assertRaises(ValueError):
                    self.record_module.cells(self.inventory, draft)

    def test_14_the_untrusted_grant_must_be_a_declared_candidate(self):
        draft = json.loads(json.dumps(self.draft))
        draft["candidates"] = [c for c in draft["candidates"]
                               if c["host"] != self.record_module.UNTRUSTED_GRANT]
        with self.assertRaises(ValueError):
            self.record_module.cells(self.inventory, draft)

    # --- surface 1: the effective rule set ---------------------------------------------------------

    def effective_doc(self, cell, extra=None, omit_denies=(), kit=True):
        sandbox = cell["name"]
        rules = list(FS_RULES)
        if kit and cell["backend"] == "claude":
            rules.append(net_rule(sandbox, "allow",
                                  [f"{h}:443" for h in CLAUDE_KIT_HOSTS], kit=True))
        for host in cell["allow_rules"]:
            rules.append(net_rule(sandbox, "allow", [host]))
        for host in cell["deny_rules"]:
            if host not in omit_denies:
                rules.append(net_rule(sandbox, "deny", [host]))
        rules.extend(extra or [])
        return {"rules": rules}

    def test_15_a_correct_effective_policy_passes(self):
        for cell in self.matrix:
            with self.subTest(cell=cell["name"]):
                self.assertEqual(
                    self.record_module.effective_policy_failures(cell, self.effective_doc(cell)), [])

    def test_16_a_missing_or_malformed_effective_document_fails(self):
        cell = self.cell("claude", "trusted")
        for bad in (None, {}, {"rules": "nonsense"}, {"rules": [None]},
                    {"rules": [{"resource_type": 5}]}):
            with self.subTest(bad=bad):
                self.assertTrue(self.record_module.effective_policy_failures(cell, bad))

    def test_17_shape_drift_in_a_network_rule_fails_closed(self):
        cell = self.cell("codex", "untrusted")
        rule = net_rule(cell["name"], "allow", ["chatgpt.com"])
        del rule["status"]
        doc = {"rules": list(FS_RULES) + [rule]}
        failures = self.record_module.effective_policy_failures(cell, doc)
        self.assertTrue(any("shape drift" in f for f in failures), failures)

    def test_18_an_unexpected_exact_allow_fails(self):
        """The latent allow: a destination no probe exercised, invisible to the other surfaces."""
        cell = self.cell("codex", "untrusted")
        doc = self.effective_doc(cell, extra=[net_rule(cell["name"], "allow", ["telemetry.example.com"])])
        failures = self.record_module.effective_policy_failures(cell, doc)
        self.assertTrue(any("telemetry.example.com" in f and "local allow" in f for f in failures),
                        failures)

    def test_19_an_unexpected_wildcard_allow_fails(self):
        cell = self.cell("codex", "untrusted")
        for wildcard in ("**", "*.npmjs.org", "*"):
            doc = self.effective_doc(cell, extra=[net_rule(cell["name"], "allow", [wildcard])])
            with self.subTest(wildcard=wildcard):
                failures = self.record_module.effective_policy_failures(cell, doc)
                self.assertTrue(any("wildcard" in f for f in failures), failures)

    def test_20_an_unexpected_global_allow_fails(self):
        cell = self.cell("codex", "untrusted")
        doc = self.effective_doc(cell, extra=[
            net_rule(cell["name"], "allow", ["github.com"], rule_id="g", scope="global")])
        failures = self.record_module.effective_policy_failures(cell, doc)
        self.assertTrue(any("GLOBAL network allow" in f for f in failures), failures)

    def test_21_a_rule_from_another_sandbox_fails(self):
        cell = self.cell("codex", "untrusted")
        doc = self.effective_doc(cell, extra=[
            net_rule(cell["name"], "allow", ["github.com"], rule_id="o", scope="sandbox:other")])
        failures = self.record_module.effective_policy_failures(cell, doc)
        self.assertTrue(any("scoped to 'sandbox:other'" in f for f in failures), failures)

    def test_22_a_kit_allow_outside_the_profile_passes_only_with_its_explicit_deny(self):
        cell = self.cell("claude", "untrusted")
        self.assertEqual(self.record_module.effective_policy_failures(cell, self.effective_doc(cell)),
                         [])
        doc = self.effective_doc(cell, omit_denies=("mcp-proxy.anthropic.com",))
        failures = self.record_module.effective_policy_failures(cell, doc)
        self.assertTrue(any("mcp-proxy.anthropic.com" in f and "no explicit" in f for f in failures),
                        failures)
        self.assertTrue(any("mcp-proxy.anthropic.com" in f and "absent" in f for f in failures),
                        failures)

    def test_23_an_inactive_deny_does_not_neutralize_a_kit_allow(self):
        cell = self.cell("claude", "untrusted")
        rules = self.effective_doc(cell, omit_denies=("claude.com",))["rules"]
        rules.append(net_rule(cell["name"], "deny", ["claude.com"], status="inactive"))
        failures = self.record_module.effective_policy_failures(cell, {"rules": rules})
        self.assertTrue(any("not active" in f for f in failures), failures)

    def test_24_a_permitted_host_with_no_allow_rule_fails(self):
        cell = self.cell("codex", "trusted")
        doc = self.effective_doc(cell)
        doc["rules"] = [r for r in doc["rules"] if r.get("resources") != ["crates.io"]]
        failures = self.record_module.effective_policy_failures(cell, doc)
        self.assertTrue(any("crates.io" in f and "no active allow rule" in f for f in failures),
                        failures)

    def test_25_a_known_permitted_local_allow_passes(self):
        cell = self.cell("codex", "trusted")
        doc = self.effective_doc(cell, extra=[
            net_rule(cell["name"], "allow", ["pypi.org:443"], rule_id="dup")])
        self.assertEqual(self.record_module.effective_policy_failures(cell, doc), [])

    # --- surfaces 2-4 --------------------------------------------------------------------------------

    def test_26_check_decisions_are_read_exactly(self):
        self.assertEqual(self.record_module.check_decision(check_doc(True)), self.record_module.ALLOW)
        self.assertEqual(self.record_module.check_decision(check_doc(False, "explicit")),
                         self.record_module.DENY_EXPLICIT)
        self.assertEqual(self.record_module.check_decision(check_doc(False, "implicit")),
                         self.record_module.DENY_IMPLICIT)
        for bad in (None, {}, {"allowed": "yes"}, {"allowed": False, "deny_kind": "other"}):
            self.assertIsNone(self.record_module.check_decision(bad))

    def test_27_the_connect_tunnel_distinguishes_allowed_from_blocked(self):
        self.assertEqual(self.record_module.probe_decision(ESTABLISHED), self.record_module.ALLOW)
        self.assertEqual(self.record_module.probe_decision(BLOCKED), "blocked")
        self.assertIsNone(self.record_module.probe_decision(""))
        self.assertIsNone(self.record_module.probe_decision(None))
        upstream_403 = "HTTP/1.0 200 Connection established\n\nHTTP/2 403 \nserver: upstream\n"
        self.assertEqual(self.record_module.probe_decision(upstream_403), self.record_module.ALLOW)

    def test_28_log_entries_are_read_by_reason(self):
        log = {
            "allowed_hosts": [{"host": "api.anthropic.com:443"}],
            "blocked_hosts": [
                {"host": "code.claude.com:443", "reason": "Denied by local rule"},
                {"host": "github.com:443", "reason": "No matching allow rule (default deny)"},
            ],
        }
        self.assertEqual(self.record_module.log_decision(log, "api.anthropic.com"),
                         self.record_module.ALLOW)
        self.assertEqual(self.record_module.log_decision(log, "code.claude.com"),
                         self.record_module.DENY_EXPLICIT)
        self.assertEqual(self.record_module.log_decision(log, "github.com"),
                         self.record_module.DENY_IMPLICIT)
        self.assertIsNone(self.record_module.log_decision(log, "pypi.org"))

    def _surfaces(self, cell):
        checks, probes = {}, {}
        allowed, blocked = [], []
        for host, decision in cell["expected"].items():
            if decision == self.record_module.ALLOW:
                checks[host] = check_doc(True)
                probes[host] = ESTABLISHED
                allowed.append({"host": f"{host}:443"})
            else:
                kind = "explicit" if decision == self.record_module.DENY_EXPLICIT else "implicit"
                checks[host] = check_doc(False, kind)
                probes[host] = BLOCKED
                blocked.append({
                    "host": f"{host}:443",
                    "reason": ("Denied by local rule" if kind == "explicit"
                               else "No matching allow rule (default deny)"),
                })
        return checks, probes, {"allowed_hosts": allowed, "blocked_hosts": blocked}

    def test_29_agreeing_surfaces_pass_the_cell(self):
        cell = self.cell("claude", "untrusted")
        checks, probes, log = self._surfaces(cell)
        self.assertEqual(self.record_module.cell_failures(cell, checks, probes, log), [])
        self.assertEqual(self.record_module.surprise_failures(cell, log), [])

    def test_30_one_disagreeing_surface_fails_the_cell(self):
        cell = self.cell("claude", "untrusted")

        checks, probes, log = self._surfaces(cell)
        probes["api.anthropic.com"] = BLOCKED
        self.assertTrue(any("connection attempt" in f
                            for f in self.record_module.cell_failures(cell, checks, probes, log)))

        checks, probes, log = self._surfaces(cell)
        checks["github.com"] = check_doc(True)
        self.assertTrue(any("policy check" in f
                            for f in self.record_module.cell_failures(cell, checks, probes, log)))

        checks, probes, log = self._surfaces(cell)
        log["blocked_hosts"] = [e for e in log["blocked_hosts"] if e["host"] != "github.com:443"]
        self.assertTrue(any("policy-log" in f
                            for f in self.record_module.cell_failures(cell, checks, probes, log)))

    def test_31_an_implicit_deny_where_an_explicit_override_was_required_fails(self):
        cell = self.cell("claude", "untrusted")
        checks, probes, log = self._surfaces(cell)
        checks["code.claude.com"] = check_doc(False, "implicit")
        failures = self.record_module.cell_failures(cell, checks, probes, log)
        self.assertTrue(any("code.claude.com" in f and "deny-explicit" in f for f in failures),
                        failures)

    def test_32_a_missing_surface_fails_closed(self):
        cell = self.cell("claude", "untrusted")
        checks, probes, log = self._surfaces(cell)
        del checks["api.anthropic.com"]
        del probes["github.com"]
        failures = self.record_module.cell_failures(cell, checks, probes, log)
        self.assertTrue(any("could not be read" in f for f in failures), failures)

    def test_33_a_destination_the_profile_never_permitted_fails_the_cell(self):
        cell = self.cell("claude", "untrusted")
        _, _, log = self._surfaces(cell)
        log["allowed_hosts"].append({"host": "telemetry.example.com:443"})
        self.assertTrue(any("telemetry.example.com:443" in s
                            for s in self.record_module.surprise_failures(cell, log)))
        self.assertTrue(self.record_module.surprise_failures(cell, None))
        self.assertTrue(self.record_module.surprise_failures(cell, {"allowed_hosts": "nonsense"}))

    # --- the global fingerprint -----------------------------------------------------------------------

    def _baseline(self):
        return {"rules": [dict(self.preflight_module.BOOTSTRAP_RULE)]}

    def test_34_the_fingerprint_ignores_sandbox_scoped_rules(self):
        base = self._baseline()
        scoped = json.loads(json.dumps(base))
        scoped["rules"].append(net_rule("x", "allow", ["api.anthropic.com:443"], kit=True))
        self.assertEqual(self.record_module.fingerprint(base, False),
                         self.record_module.fingerprint(scoped, False))

    def test_35_the_fingerprint_moves_when_global_policy_or_governance_changes(self):
        base = self._baseline()
        widened = json.loads(json.dumps(base))
        widened["rules"][0]["decision"] = "allow"
        self.assertNotEqual(self.record_module.fingerprint(base, False),
                            self.record_module.fingerprint(widened, False))

        added = json.loads(json.dumps(base))
        added["rules"].append(net_rule("x", "allow", ["github.com:443"], rule_id="extra",
                                       scope="global"))
        self.assertNotEqual(self.record_module.fingerprint(base, False),
                            self.record_module.fingerprint(added, False))
        self.assertNotEqual(self.record_module.fingerprint(base, False),
                            self.record_module.fingerprint(base, True))

    def test_36_an_unreadable_policy_has_no_fingerprint(self):
        self.assertIsNone(self.record_module.fingerprint(None, False))
        self.assertIsNone(self.record_module.fingerprint({"rules": "nonsense"}, False))

    # --- the recorder end to end -----------------------------------------------------------------------

    def _write_work(self):
        (self.work / "pf-version.json").write_text(json.dumps({
            "client": {"version": self.pins["sbx"]["exact"]},
            "server": {"version": self.pins["sbx"]["exact"], "state": "running"},
        }), encoding="utf-8")
        (self.work / "pf-ssh-forwarding.json").write_text(
            json.dumps({"key": "ssh.agentForwardingEnabled", "value": False}), encoding="utf-8")
        (self.work / "pf-ssh-socket.json").write_text(
            json.dumps({"key": "ssh.agentSocketPath", "value": ""}), encoding="utf-8")
        baseline = {"rules": [dict(self.preflight_module.BOOTSTRAP_RULE)]}
        (self.work / "pf-policy.json").write_text(json.dumps(baseline), encoding="utf-8")
        (self.work / "policy-after.json").write_text(json.dumps(baseline), encoding="utf-8")
        (self.work / "pf-ls.json").write_text(json.dumps({"sandboxes": []}), encoding="utf-8")
        (self.work / "ls-after.json").write_text(json.dumps({"sandboxes": []}), encoding="utf-8")
        for which in ("before", "after"):
            (self.work / f"governance-{which}.json").write_text(
                json.dumps(governance_doc()), encoding="utf-8")

        for cell in self.matrix:
            checks, probes, log = self._surfaces(cell)
            for host, doc in checks.items():
                (self.work / f"check-{cell['name']}-{host}.json").write_text(
                    json.dumps(doc), encoding="utf-8")
            text = "probe_tool=curl\n" + "".join(
                f"### host={host}\n{body}" for host, body in probes.items())
            (self.work / f"probe-{cell['name']}.txt").write_text(text, encoding="utf-8")
            (self.work / f"log-{cell['name']}.json").write_text(json.dumps(log), encoding="utf-8")
            (self.work / f"effective-{cell['name']}.json").write_text(
                json.dumps(self.effective_doc(cell)), encoding="utf-8")

    def _capture(self, **overrides):
        obs = {
            "sbx_env_ssh_auth_sock": "removed",
            "pf_version_exit": "0", "pf_ssh_forwarding_exit": "0", "pf_ssh_socket_exit": "0",
            "pf_policy_exit": "0", "pf_ls_exit": "0", "plan_exit": "0",
            "governance_before_exit": "1", "governance_after_exit": "1",
            "ls_after_exit": "0", "policy_after_exit": "0",
            "t014_git_clean": "yes",
        }
        # The run binds itself to the committed T014 artifacts; the synthetic capture records the
        # digests those files really have, so only a deliberate mutation breaks the binding.
        obs.update({f"t014_{name}": digest
                    for name, digest in self.record_module.input_digests().items()})
        for cell in self.matrix:
            for key in ("create", "rules", "effective", "probe", "log", "rm"):
                obs[f"{key}_{cell['name']}_exit"] = "0"
            obs[f"probe_tool_{cell['name']}"] = "curl"
        obs.update(overrides)
        return obs

    def _results(self, obs):
        criteria, _ = self.record_module.evaluate(
            obs, str(self.work), inventory_file=str(INVENTORY_FILE), draft_file=str(DRAFT_FILE))
        return {row["id"]: row["result"] for row in criteria}

    def test_37_a_clean_capture_passes_every_criterion(self):
        results = self._results(self._capture())
        self.assertTrue(results)
        self.assertEqual(set(results.values()), {"PASS"}, results)

    def test_38_every_cell_has_all_three_criteria(self):
        results = self._results(self._capture())
        for backend in ("claude", "codex"):
            for profile in ("trusted", "untrusted"):
                for suffix in ("", ".exact", ".effective"):
                    self.assertIn(f"G4.{backend}.{profile}{suffix}", results)

    def test_39_an_effective_policy_read_failure_cannot_pass(self):
        """The regression this fixes: the snapshot was captured but never evaluated."""
        results = self._results(self._capture(**{"effective_dca-g4-claude-trusted_exit": "1"}))
        self.assertEqual(results["G4.claude.trusted.effective"], "FAIL")

    def test_40_a_missing_effective_document_cannot_pass(self):
        (self.work / "effective-dca-g4-codex-trusted.json").unlink()
        self.assertEqual(self._results(self._capture())["G4.codex.trusted.effective"], "FAIL")

    def test_41_a_latent_allow_fails_even_when_every_probe_looks_correct(self):
        """No probe exercises this host, so only the effective rule set can catch it."""
        cell = self.cell("claude", "untrusted")
        doc = self.effective_doc(cell, extra=[
            net_rule(cell["name"], "allow", ["exfil.example.com"])])
        (self.work / f"effective-{cell['name']}.json").write_text(json.dumps(doc), encoding="utf-8")
        results = self._results(self._capture())
        self.assertEqual(results["G4.claude.untrusted"], "PASS")
        self.assertEqual(results["G4.claude.untrusted.exact"], "PASS")
        self.assertEqual(results["G4.claude.untrusted.effective"], "FAIL")

    def test_42_a_failed_preflight_leaves_the_cells_not_run(self):
        (self.work / "pf-ls.json").write_text(
            json.dumps({"sandboxes": [{"name": "leftover"}]}), encoding="utf-8")
        results = self._results(self._capture())
        self.assertEqual(results["G4.0d"], "FAIL")
        self.assertEqual(results["G4.claude.trusted"], "NOT-RUN")
        self.assertEqual(results["G4.claude.trusted.effective"], "NOT-RUN")

    def test_43_a_kit_allow_that_was_not_overridden_fails_the_gate(self):
        (self.work / "check-dca-g4-claude-untrusted-code.claude.com.json").write_text(
            json.dumps(check_doc(True)), encoding="utf-8")
        results = self._results(self._capture())
        self.assertEqual(results["G4.kit-override"], "FAIL")
        self.assertEqual(results["G4.claude.untrusted"], "FAIL")

    def test_44_a_silently_promoted_host_fails_the_gate(self):
        (self.work / "check-dca-g4-codex-trusted-auth.openai.com.json").write_text(
            json.dumps(check_doc(True)), encoding="utf-8")
        self.assertEqual(self._results(self._capture())["G4.no-promotion"], "FAIL")

    def test_45_a_moved_fingerprint_fails_the_gate(self):
        widened = {"rules": [dict(self.preflight_module.BOOTSTRAP_RULE),
                             net_rule("x", "allow", ["github.com:443"], rule_id="extra",
                                      scope="global")]}
        (self.work / "policy-after.json").write_text(json.dumps(widened), encoding="utf-8")
        results = self._results(self._capture())
        self.assertEqual(results["G4.fingerprint"], "FAIL")
        self.assertEqual(results["G4.clean"], "FAIL")

    def test_46_a_governance_change_fails_the_fingerprint(self):
        self._governance("after", governance_doc(active=True))
        results = self._results(self._capture())
        self.assertEqual(results["G4.governance"], "FAIL")
        self.assertEqual(results["G4.fingerprint"], "FAIL")

    def test_47_a_leftover_sandbox_fails_cleanup(self):
        (self.work / "ls-after.json").write_text(
            json.dumps({"sandboxes": [{"name": "dca-g4-codex-untrusted"}]}), encoding="utf-8")
        self.assertEqual(self._results(self._capture())["G4.clean"], "FAIL")

    # --- the machine-readable fingerprint in the evidence -----------------------------------------------

    def _record_to(self, obs, name="G4.json"):
        obs_path = self.tmp / "observations.env"
        obs_path.write_text("".join(f"{k}={v}\n" for k, v in obs.items()), encoding="utf-8")
        evidence_path = self.tmp / name
        status, _ = self.record_module.record(
            str(obs_path), str(self.work), str(ROOT / "runtime" / "versions.yaml"),
            str(evidence_path))
        return status, json.loads(evidence_path.read_text(encoding="utf-8"))

    def test_48_a_pass_writes_the_fingerprint_as_a_typed_field(self):
        status, evidence = self._record_to(self._capture())
        self.assertEqual(status, "PASS")
        expected = self.record_module.fingerprint(
            json.loads((self.work / "pf-policy.json").read_text(encoding="utf-8")), False)
        self.assertEqual(evidence["network_policy_fingerprint"], expected)
        self.assertRegex(evidence["network_policy_fingerprint"], r"^sha256:[0-9a-f]{64}$")
        jsonschema.Draft202012Validator(self.evidence_schema).validate(evidence)
        self.assertEqual(evidence["gate"], "G4")
        self.assertEqual(self.rules.evidence_problems(evidence, self.pins), [])
        # The proven set is the typed field now; the prose copy is gone on purpose.
        self.assertNotIn("Proven allow set", evidence["notes"])
        self.assertEqual(evidence["proven_network_allowset"],
                         self.record_module.proven_host_set(self.matrix))

    def test_49_a_g4_pass_without_the_fingerprint_is_schema_invalid(self):
        _, evidence = self._record_to(self._capture())
        del evidence["network_policy_fingerprint"]
        with self.assertRaises(jsonschema.ValidationError):
            jsonschema.Draft202012Validator(self.evidence_schema).validate(evidence)

    def test_50_a_malformed_fingerprint_is_schema_invalid(self):
        _, evidence = self._record_to(self._capture())
        for bad in ("deadbeef", "sha256:XYZ", "sha256:" + "f" * 63, None):
            evidence["network_policy_fingerprint"] = bad
            with self.subTest(bad=bad):
                with self.assertRaises(jsonschema.ValidationError):
                    jsonschema.Draft202012Validator(self.evidence_schema).validate(evidence)

    def test_51_a_fingerprint_mismatch_is_not_recorded_as_a_fingerprint(self):
        self._governance("after", governance_doc(active=True))
        status, evidence = self._record_to(self._capture())
        self.assertEqual(status, "FAIL")
        self.assertNotIn("network_policy_fingerprint", evidence)
        jsonschema.Draft202012Validator(self.evidence_schema).validate(evidence)

    # --- governance is observed fail-closed -----------------------------------------------------

    def test_53_a_decided_governance_probe_is_read_as_a_typed_boolean(self):
        for active in (False, True):
            with self.subTest(active=active):
                value, why = self.record_module.governance_active(governance_doc(active), "1")
                self.assertIsNone(why)
                self.assertIs(value, active)

    def test_54_an_unread_governance_probe_never_yields_a_value(self):
        cases = {
            "not recorded at all": (governance_doc(), None),
            "no document": (None, "1"),
            "not a document": ("ERROR: boom", "1"),
            "usage error shape": ({"error": "unknown flag"}, "1"),
            "wrong target": (governance_doc(target="other.invalid:443"), "1"),
            "undecided": ({**governance_doc(), "allowed": None}, "1"),
            "no governance object": ({k: v for k, v in governance_doc().items()
                                      if k != "governance"}, "1"),
            "governance not an object": ({**governance_doc(), "governance": "false"}, "1"),
            "no active field": ({**governance_doc(), "governance": {}}, "1"),
            "active is the string false": ({**governance_doc(),
                                            "governance": {"active": "false"}}, "1"),
            "active is an int": ({**governance_doc(), "governance": {"active": 0}}, "1"),
        }
        for why, (document, exit_code) in cases.items():
            with self.subTest(case=why):
                value, problem = self.record_module.governance_active(document, exit_code)
                self.assertIsNone(value)
                self.assertTrue(problem)

    def test_55_an_unread_governance_status_is_never_hashed(self):
        base = self._baseline()
        for bad in (None, "false", "true", 0, 1, {}):
            with self.subTest(bad=bad):
                self.assertIsNone(self.record_module.fingerprint(base, bad))

    def test_56_a_failed_governance_command_fails_the_gate(self):
        """The regression this fixes: an unreadable governance status used to PASS."""
        self._governance("before", "ERROR: global network policy has not been initialized")
        results = self._results(self._capture())
        self.assertEqual(results["G4.governance"], "FAIL")
        self.assertEqual(results["G4.fingerprint"], "FAIL")

    def test_57_a_missing_governance_capture_fails_the_gate(self):
        self._governance("after", None)
        self.assertEqual(self._results(self._capture())["G4.governance"], "FAIL")

    def test_58_a_governance_probe_without_active_fails_the_gate(self):
        self._governance("before", {k: v for k, v in governance_doc().items() if k != "governance"})
        self.assertEqual(self._results(self._capture())["G4.governance"], "FAIL")

    def test_59_a_string_governance_status_fails_the_gate(self):
        self._governance("before", {**governance_doc(), "governance": {"active": "false"}})
        self.assertEqual(self._results(self._capture())["G4.governance"], "FAIL")

    def test_60_an_unrecorded_governance_exit_fails_the_gate(self):
        obs = self._capture()
        del obs["governance_before_exit"]
        self.assertEqual(self._results(obs)["G4.governance"], "FAIL")

    def test_61_a_governance_probe_that_never_ran_leaves_the_criterion_not_run(self):
        obs = self._capture()
        del obs["governance_before_exit"]
        del obs["governance_after_exit"]
        self.assertEqual(self._results(obs)["G4.governance"], "NOT-RUN")

    # --- the fingerprint covers every semantically relevant field --------------------------------

    def test_62_every_security_relevant_global_rule_field_moves_the_digest(self):
        base = self._baseline()
        reference = self.record_module.fingerprint(base, False)
        mutations = {
            "id": "other-rule",
            "name": "renamed",
            "applies_to": "some-sandbox",
            "resource_type": "network:egress",
            "decision": "allow",
            "resources": ["github.com:443"],
            "origin": "remote",
            "layer": "org",
            "status": "inactive",
            "editable": True,
        }
        for field, value in mutations.items():
            moved = json.loads(json.dumps(base))
            moved["rules"][0][field] = value
            with self.subTest(field=field):
                self.assertNotEqual(self.record_module.fingerprint(moved, False), reference,
                                    f"a change to {field} left the fingerprint unmoved")
        with self.subTest(field="governance.active"):
            self.assertNotEqual(self.record_module.fingerprint(base, True), reference)

    def test_63_the_digest_is_stable_under_rule_order_and_resource_order(self):
        base = self._baseline()
        base["rules"][0]["resources"] = ["**", "aaa.example.com"]
        extra = net_rule("x", "deny", ["zzz.example.com"], rule_id="second", scope="global")
        one = {"rules": [base["rules"][0], extra]}
        other = {"rules": [extra, dict(base["rules"][0],
                                       resources=["aaa.example.com", "**"])]}
        self.assertEqual(self.record_module.fingerprint(one, False),
                         self.record_module.fingerprint(other, False))

    def test_64_sandbox_scoped_rules_and_grants_stay_fingerprint_neutral(self):
        base = self._baseline()
        noisy = json.loads(json.dumps(base))
        noisy["rules"].append(net_rule("x", "allow", ["api.anthropic.com:443"], kit=True))
        noisy["rules"].append(net_rule("y", "deny", ["github.com"]))
        noisy["rules"].extend(json.loads(json.dumps(FS_RULES)))
        self.assertEqual(self.record_module.fingerprint(base, False),
                         self.record_module.fingerprint(noisy, False))

    def test_65_the_canonical_input_is_exactly_what_the_schema_documents(self):
        base = self._baseline()
        value = self.record_module.fingerprint_input(base, False)
        self.assertEqual(set(value), {"global_network_rules", "governance"})
        self.assertEqual(value["governance"], {"active": False})
        self.assertEqual(set(value["global_network_rules"][0]),
                         set(self.record_module.FINGERPRINT_RULE_KEYS))
        described = self.evidence_schema["properties"]["network_policy_fingerprint"]["description"]
        for field in self.record_module.FINGERPRINT_RULE_KEYS:
            with self.subTest(field=field):
                self.assertIn(field, described)
        digest = "sha256:" + __import__("hashlib").sha256(
            self.rules.canonical_json(value).encode("utf-8")).hexdigest()
        self.assertEqual(self.record_module.fingerprint(base, False), digest)

    # --- the prerequisite restoration is bound to the run that used it ---------------------------

    def _restoration(self, fingerprint_after):
        path = self.tmp / "restoration.json"
        body = json.dumps({
            "what": "SYNTHETIC",
            "verification": {
                "matches_documented_bootstrap_rule_field_by_field": True,
                "restored_state_matches_prior_g4_baseline": True,
                "network_policy_fingerprint_after": fingerprint_after,
            },
        }, indent=2)
        path.write_text(body, encoding="utf-8")
        return path, self.record_module.restoration_digest(body)

    def _baseline_fingerprint(self):
        return self.record_module.fingerprint(
            json.loads((self.work / "pf-policy.json").read_text(encoding="utf-8")), False)

    def test_66_a_run_that_consumed_the_restoration_is_bound_to_it(self):
        before = self._baseline_fingerprint()
        path, digest = self._restoration(before)
        restoration, binding = self.record_module.consumed_restoration(
            {"prereq_restoration_digest": digest}, before, prereq_file=str(path))
        self.assertIsNotNone(restoration)
        self.assertEqual(binding["digest"], digest)
        self.assertIs(binding["consumed_by_this_run"], True)
        self.assertEqual(binding["baseline_fingerprint"], before)

    def test_67_a_later_run_does_not_inherit_a_restoration_it_never_consumed(self):
        """The durable record still exists; this run simply did not claim it."""
        before = self._baseline_fingerprint()
        path, _ = self._restoration(before)
        self.assertTrue(path.exists())
        restoration, binding = self.record_module.consumed_restoration(
            {}, before, prereq_file=str(path))
        self.assertIsNone(restoration)
        self.assertIsNone(binding)

    def test_68_an_edited_or_mismatched_restoration_is_refused(self):
        before = self._baseline_fingerprint()
        path, digest = self._restoration(before)
        cases = {
            "record edited after the run claimed it": (
                digest, before, lambda: path.write_text("{}", encoding="utf-8")),
            "claimed digest is not this record": ("sha256:" + "0" * 64, before, None),
            "run started from a different baseline": (
                digest, "sha256:" + "1" * 64, None),
            "this run has no baseline at all": (digest, None, None),
            "record unreadable": (digest, before,
                                  lambda: path.write_text("not json", encoding="utf-8")),
        }
        for why, (claimed, base, mutate) in cases.items():
            if mutate:
                mutate()
            with self.subTest(case=why):
                restoration, binding = self.record_module.consumed_restoration(
                    {"prereq_restoration_digest": claimed}, base, prereq_file=str(path))
                self.assertIsNone(restoration)
                self.assertIsNone(binding)

    def test_69_the_recorded_evidence_carries_the_binding_only_when_it_applies(self):
        """Both branches are driven explicitly. Asserting one thing when the binding is present
        and another when it is absent passes either way, so it cannot bite - and with the default
        synthetic baseline only the absent branch was ever reachable."""
        committed = GATES / "G4" / "prereq" / "restoration.json"
        text = committed.read_text(encoding="utf-8")
        digest = self.record_module.restoration_digest(text)
        verification = json.loads(text)["verification"]

        # Condition 3 is that the run started from the baseline the restoration produced, so the
        # capture is given exactly the global state the record says it left behind. The record
        # carries that state verbatim as the canonical fingerprint input.
        produced = json.loads(verification["fingerprint_canonical_input"])
        baseline = {"rules": produced["global_network_rules"]}
        for name in ("pf-policy.json", "policy-after.json"):
            (self.work / name).write_text(json.dumps(baseline), encoding="utf-8")
        self.assertEqual(
            self.record_module.fingerprint(baseline, False),
            verification["network_policy_fingerprint_after"],
            "the committed restoration record must describe the baseline it claims to have produced")

        # Claimed: the run observed the record, the bytes are unchanged, and the baseline matches.
        status, claimed = self._record_to(self._capture(prereq_restoration_digest=digest))
        self.assertEqual(status, "PASS")
        self.assertEqual(claimed["prerequisite_restoration"], {
            "digest": digest,
            "consumed_by_this_run": True,
            "baseline_fingerprint": verification["network_policy_fingerprint_after"],
        })
        self.assertIn("PREREQUISITE RESTORATION", claimed["notes"])
        self.assertIn(digest, claimed["notes"])
        jsonschema.Draft202012Validator(self.evidence_schema).validate(claimed)

        # Not claimed: the durable record is still on disk, so only the run binding keeps a later
        # rerun from inheriting a restoration it never consumed.
        for obs in (self._capture(),
                    self._capture(prereq_restoration_digest="sha256:" + "0" * 64)):
            with self.subTest(claim=obs.get("prereq_restoration_digest")):
                _, evidence = self._record_to(obs, name="G4-unclaimed.json")
                self.assertTrue(committed.exists())
                self.assertNotIn("prerequisite_restoration", evidence)
                self.assertNotIn("PREREQUISITE RESTORATION", evidence["notes"])
                jsonschema.Draft202012Validator(self.evidence_schema).validate(evidence)

        # A run that started from a DIFFERENT baseline may not claim the record either, even with
        # the correct digest.
        for name in ("pf-policy.json", "policy-after.json"):
            (self.work / name).write_text(
                json.dumps({"rules": [dict(self.preflight_module.BOOTSTRAP_RULE)]}),
                encoding="utf-8")
        _, moved = self._record_to(self._capture(prereq_restoration_digest=digest),
                                   name="G4-moved.json")
        self.assertNotIn("prerequisite_restoration", moved)
        self.assertNotIn("PREREQUISITE RESTORATION", moved["notes"])

    def test_70_only_g4_may_carry_a_prerequisite_restoration_binding(self):
        _, evidence = self._record_to(self._capture())
        evidence["prerequisite_restoration"] = {
            "digest": "sha256:" + "a" * 64,
            "consumed_by_this_run": True,
            "baseline_fingerprint": "sha256:" + "b" * 64,
        }
        validator = jsonschema.Draft202012Validator(self.evidence_schema)
        validator.validate(evidence)
        for bad in ({"digest": "sha256:" + "a" * 64, "consumed_by_this_run": False,
                     "baseline_fingerprint": "sha256:" + "b" * 64},
                    {"digest": "nope", "consumed_by_this_run": True,
                     "baseline_fingerprint": "sha256:" + "b" * 64},
                    {"digest": "sha256:" + "a" * 64, "consumed_by_this_run": True}):
            with self.subTest(bad=bad):
                with self.assertRaises(jsonschema.ValidationError):
                    validator.validate(dict(evidence, prerequisite_restoration=bad))
        with self.assertRaises(jsonschema.ValidationError):
            validator.validate(dict(evidence, gate="G5"))

    # --- G4 is bound to the reviewed, committed T014 inputs --------------------------------------

    def test_71_clean_reviewed_inputs_bind_the_run(self):
        results = self._results(self._capture())
        self.assertEqual(results["G4.inputs"], "PASS")
        digests, problems = self.record_module.input_binding_problems(self._capture())
        self.assertEqual(problems, [])
        self.assertEqual(set(digests), {n for n, _ in self.record_module.T014_INPUTS})
        for value in digests.values():
            self.assertRegex(value, r"^sha256:[0-9a-f]{64}$")

    def test_72_a_t014_input_edited_after_the_run_fails_before_any_policy_work(self):
        """A draft or inventory edited after T014 must not silently produce a new allow set."""
        for name, _ in self.record_module.T014_INPUTS:
            obs = self._capture(**{f"t014_{name}": "sha256:" + "0" * 64})
            results = self._results(obs)
            with self.subTest(input=name):
                self.assertEqual(results["G4.inputs"], "FAIL")
                # Fail-closed: nothing about the live policy is then claimed.
                for suffix in ("", ".exact", ".effective"):
                    self.assertEqual(results[f"G4.claude.trusted{suffix}"], "NOT-RUN")
                self.assertEqual(results["G4.kit-override"], "NOT-RUN")

    def test_73_dirty_or_untracked_t014_inputs_fail_the_gate(self):
        for value in ("no", "", "maybe"):
            with self.subTest(t014_git_clean=value):
                results = self._results(self._capture(t014_git_clean=value))
                self.assertEqual(results["G4.inputs"], "FAIL")
        obs = self._capture()
        del obs["t014_git_clean"]
        self.assertEqual(self._results(obs)["G4.inputs"], "NOT-RUN")

    def test_74_a_failed_or_stale_t014_gate_evidence_cannot_underwrite_g4(self):
        good = json.loads((GATES / "INVENTORY.json").read_text(encoding="utf-8"))
        cases = {
            "not PASS": dict(good, status="FAIL"),
            "wrong gate": dict(good, gate="G5"),
            "stale provenance": {**good, "provenance": {
                "runtime_versions_digest": "sha256:" + "0" * 64}},
            "unreadable": None,
        }
        for why, document in cases.items():
            path = self.tmp / "INVENTORY.json"
            path.write_text("not json" if document is None else json.dumps(document),
                            encoding="utf-8")
            with self.subTest(case=why):
                _, problems = self.record_module.input_binding_problems(
                    self._capture(), inventory_evidence=str(path))
                self.assertTrue(problems, why)

    def test_75_a_candidate_insertion_cannot_silently_widen_trusted(self):
        """The mutation the binding exists to stop, shown end to end."""
        widened = json.loads(json.dumps(self.draft))
        widened["candidates"].append({
            "host": "mcp-proxy.anthropic.com", "port": 443, "category": "documentation",
            "approved": False, "evidence_ref": "research.md R12", "rationale": "x",
        })
        # On its own the edit is syntactically legal and WOULD widen both trusted cells...
        cells = self.record_module.cells(self.inventory, widened)
        trusted = next(c for c in cells if c["name"] == "dca-g4-claude-trusted")
        self.assertEqual(trusted["expected"]["mcp-proxy.anthropic.com"], self.record_module.ALLOW)
        self.assertNotIn("mcp-proxy.anthropic.com", trusted["deny_rules"])
        # ...but the edited file no longer matches the digest the run recorded, so G4 fails closed
        # instead of proving the widened policy.
        edited = self.record_module.restoration_digest(json.dumps(widened))
        obs = self._capture(t014_trusted_allowlist_draft_sha256=edited)
        self.assertEqual(self._results(obs)["G4.inputs"], "FAIL")

    # --- the matrix honours each inventory entry's profiles --------------------------------------

    def _inventory_with(self, backend, target, fields=None, **kwargs):
        """The committed inventory with one entry's fields replaced."""
        changes = dict(fields or {}, **kwargs)
        inventory = json.loads(json.dumps(self.inventory))
        for entry in inventory["backends"][backend]["hosts"]:
            if entry["host"] == target:
                entry.update(changes)
                return inventory
        raise AssertionError(f"{target} is not in the {backend} inventory")

    def _permitted(self, cells, name):
        cell = next(c for c in cells if c["name"] == name)
        return {h for h, d in cell["expected"].items() if d == self.record_module.ALLOW}, cell

    def test_76_a_trusted_only_refresh_host_never_widens_untrusted(self):
        """The documented promotion path: untrusted access is never broadened by a promotion."""
        inventory = self._inventory_with(
            "claude", "platform.claude.com",
            purpose="refresh", sandbox_required=True, profiles=["trusted"])
        cells = self.record_module.cells(inventory, self.draft)
        trusted, _ = self._permitted(cells, "dca-g4-claude-trusted")
        untrusted, untrusted_cell = self._permitted(cells, "dca-g4-claude-untrusted")
        self.assertIn("platform.claude.com", trusted)
        self.assertNotIn("platform.claude.com", untrusted)
        self.assertEqual(untrusted_cell["expected"]["platform.claude.com"],
                         self.record_module.DENY_EXPLICIT)
        self.assertIn("platform.claude.com", untrusted_cell["deny_rules"])
        self.assertTrue(untrusted < trusted)

    def test_77_an_untrusted_only_required_host_is_permitted_only_there(self):
        inventory = self._inventory_with(
            "codex", "chatgpt.com", profiles=["untrusted"])
        # The trusted cell then has no control plane at all, which is itself a stop condition.
        with self.assertRaises(ValueError):
            self.record_module.cells(inventory, self.draft)
        inventory = self._inventory_with("claude", "code.claude.com",
                                         sandbox_required=True, profiles=["untrusted"])
        cells = self.record_module.cells(inventory, self.draft)
        trusted, trusted_cell = self._permitted(cells, "dca-g4-claude-trusted")
        untrusted, _ = self._permitted(cells, "dca-g4-claude-untrusted")
        self.assertIn("code.claude.com", untrusted)
        # Still permitted for trusted, but as a draft candidate rather than as a required host.
        self.assertIn("code.claude.com", trusted)
        self.assertIn("code.claude.com", self.candidates)
        self.assertNotIn("code.claude.com", trusted_cell["deny_rules"])

    def test_78_both_profiles_means_both_cells(self):
        for backend, host in (("claude", "api.anthropic.com"), ("codex", "chatgpt.com")):
            with self.subTest(backend=backend):
                for profile in ("trusted", "untrusted"):
                    self.assertEqual(self.cell(backend, profile)["expected"][host],
                                     self.record_module.ALLOW)

    def test_79_malformed_inventory_profiles_fail_closed(self):
        cases = {
            "required with no profile": ("api.anthropic.com", {"profiles": []}),
            "unknown profile": ("api.anthropic.com", {"profiles": ["staging"]}),
            "repeated profile": ("api.anthropic.com", {"profiles": ["trusted", "trusted"]}),
            "profiles not a list": ("api.anthropic.com", {"profiles": "trusted"}),
            "profiles not strings": ("api.anthropic.com", {"profiles": [1]}),
            "not required yet scoped": ("claude.com", {"profiles": ["trusted"]}),
            "sandbox_required not a boolean": ("api.anthropic.com", {"sandbox_required": "yes"}),
        }
        for why, (host, fields) in cases.items():
            inventory = self._inventory_with("claude", host, **fields)
            with self.subTest(case=why):
                with self.assertRaises(ValueError):
                    self.record_module.cells(inventory, self.draft)

    def test_80_a_malformed_inventory_host_fails_before_any_live_policy_work(self):
        cases = {
            "whitespace": "api.anthropic .com",
            "uppercase": "API.anthropic.com",
            "shell metacharacter": "api.anthropic.com;id",
            "command substitution": "$(id).com",
            "quote": 'api."com',
            "scheme": "https://api.anthropic.com",
            "embedded path": "api.anthropic.com/v1",
            "not a string": 443,
            "bare label": "localhost",
        }
        for why, host in cases.items():
            inventory = self._inventory_with("claude", "api.anthropic.com", {"host": host})
            with self.subTest(case=why):
                with self.assertRaises(ValueError):
                    self.record_module.cells(inventory, self.draft)
        wrong_port = self._inventory_with("claude", "api.anthropic.com", port=80)
        with self.assertRaises(ValueError):
            self.record_module.cells(wrong_port, self.draft)

    # --- a permitted host needs G4's OWN allow, not the kit's ------------------------------------

    def test_81_a_permitted_host_backed_only_by_the_kit_fails(self):
        """The evidence claims self-sufficiency; crediting the kit's allow would leave it untested."""
        cell = self.cell("claude", "trusted")
        doc = self.effective_doc(cell)
        doc["rules"] = [r for r in doc["rules"]
                        if not (r.get("decision") == "allow"
                                and r.get("resources") == ["api.anthropic.com"]
                                and r.get("editable") is True)]
        failures = self.record_module.effective_policy_failures(cell, doc)
        self.assertTrue(any("api.anthropic.com" in f and "only the built-in kit" in f
                            for f in failures), failures)

    def test_82_a_kit_allow_plus_g4s_own_allow_passes(self):
        cell = self.cell("claude", "trusted")
        for host in ("api.anthropic.com", "code.claude.com"):
            with self.subTest(host=host):
                self.assertIn(host, cell["allow_rules"])
        self.assertEqual(self.record_module.effective_policy_failures(
            cell, self.effective_doc(cell)), [])

    def test_83_a_kit_named_or_non_editable_rule_is_never_credited_as_g4s_own(self):
        cell = self.cell("codex", "untrusted")
        for marker in ({"kit": True}, {"rule_id": "x"}):
            doc = self.effective_doc(cell, kit=False)
            doc["rules"] = [r for r in doc["rules"] if r.get("resources") != ["chatgpt.com"]]
            rule = net_rule(cell["name"], "allow", ["chatgpt.com"], **marker)
            if "kit" not in marker:
                rule["editable"] = False          # non-editable alone is enough to disqualify it
            doc["rules"].append(rule)
            with self.subTest(marker=marker):
                failures = self.record_module.effective_policy_failures(cell, doc)
                self.assertTrue(any("only the built-in kit" in f for f in failures), failures)

    def test_84_a_non_permitted_kit_allow_still_needs_its_explicit_deny(self):
        cell = self.cell("claude", "untrusted")
        self.assertEqual(self.record_module.effective_policy_failures(
            cell, self.effective_doc(cell)), [])
        doc = self.effective_doc(cell, omit_denies=("code.claude.com",))
        self.assertTrue(self.record_module.effective_policy_failures(cell, doc))

    # --- the proven allow set is typed and PASS-only ----------------------------------------------

    def test_85_a_pass_writes_the_exact_proven_set_as_a_typed_field(self):
        status, evidence = self._record_to(self._capture())
        self.assertEqual(status, "PASS")
        proven = evidence["proven_network_allowset"]
        self.assertEqual(set(proven), {"claude", "codex"})
        for backend in ("claude", "codex"):
            for profile in ("trusted", "untrusted"):
                expected = sorted(h for h, d in self.cell(backend, profile)["expected"].items()
                                  if d == self.record_module.ALLOW)
                with self.subTest(backend=backend, profile=profile):
                    self.assertEqual(proven[backend][profile], expected)
                    self.assertEqual(proven[backend][profile], sorted(set(expected)))
        self.assertTrue(set(proven["claude"]["untrusted"]) < set(proven["claude"]["trusted"]))
        jsonschema.Draft202012Validator(self.evidence_schema).validate(evidence)

    def test_86_a_failing_gate_emits_no_proven_set_at_all(self):
        status, evidence = self._record_to(self._capture(t014_git_clean="no"))
        self.assertEqual(status, "FAIL")
        self.assertNotIn("proven_network_allowset", evidence)
        self.assertNotIn("Proven allow set", evidence["notes"])
        jsonschema.Draft202012Validator(self.evidence_schema).validate(evidence)

    def test_87_the_proven_set_is_required_of_a_pass_and_forbidden_otherwise(self):
        _, passing = self._record_to(self._capture())
        validator = jsonschema.Draft202012Validator(self.evidence_schema)
        without = {k: v for k, v in passing.items() if k != "proven_network_allowset"}
        with self.assertRaises(jsonschema.ValidationError):
            validator.validate(without)
        for status in ("FAIL", "NOT-RUN"):
            doc = dict(passing, status=status)
            if status == "NOT-RUN":
                doc["not_run_reason"] = "SYNTHETIC"
            with self.subTest(status=status):
                with self.assertRaises(jsonschema.ValidationError):
                    validator.validate(doc)
        with self.subTest(case="another gate may not carry one"):
            with self.assertRaises(jsonschema.ValidationError):
                validator.validate(dict(passing, gate="G5"))
        for bad in ({"claude": passing["proven_network_allowset"]["claude"]},
                    {**passing["proven_network_allowset"], "extra": {}},
                    {"claude": {"trusted": ["B.example.com"], "untrusted": []},
                     "codex": passing["proven_network_allowset"]["codex"]}):
            with self.subTest(bad=bad):
                with self.assertRaises(jsonschema.ValidationError):
                    validator.validate(dict(passing, proven_network_allowset=bad))

    def test_52_the_committed_g4_evidence_is_current_and_carries_the_fingerprint(self):
        evidence = json.loads((GATES / "G4.json").read_text(encoding="utf-8"))
        jsonschema.Draft202012Validator(self.evidence_schema).validate(evidence)
        self.assertEqual(evidence["gate"], "G4")
        self.assertEqual(evidence["status"], "PASS")
        self.assertRegex(evidence["network_policy_fingerprint"], r"^sha256:[0-9a-f]{64}$")
        self.assertEqual(self.rules.evidence_problems(evidence, self.pins), [])
        self.assertTrue(all(c["result"] == "PASS" for c in evidence["criteria"]), evidence["criteria"])

    # --- the runner's own invariants, and the two seams no synthetic capture reaches ---------------

    def test_88_run_sh_keeps_the_gate_invariants(self):
        """G4 was the only gate with no test that reads its run.sh, so every property enforced
        solely in the runner - the agent socket, --sandbox scoping, mountless creates, cleanup by
        name - had nothing biting on it."""
        script = (GATES / "G4" / "run.sh").read_text(encoding="utf-8")
        body = "\n".join(l for l in script.splitlines() if not l.lstrip().startswith("#"))

        # R14: one wrapper strips the agent socket, and no sbx call bypasses it.
        self.assertIn('run_sbx() {\n    env -u SSH_AUTH_SOCK sbx "$@"', body)
        for line in body.splitlines():
            if line.strip().startswith("sbx ") or " sbx " in line:
                self.assertIn("env -u SSH_AUTH_SOCK sbx", line, line)

        # The R14 observation must measure that wrapper. A probe that strips the socket on its own
        # line reports `removed` whatever run_sbx does, so G4.0b's R14 clause could never fail.
        start = body.index("record sbx_env_ssh_auth_sock")
        probe = body[start:body.index(')"', start)]
        self.assertIn("run_sbx", probe)
        self.assertNotIn("env -u SSH_AUTH_SOCK sh", probe)

        # Nothing global is mutated.
        for forbidden in ("rm --all", "sbx reset", "policy init", "policy rm", "policy reset",
                          "settings set"):
            self.assertNotIn(forbidden, body, forbidden)

        # Every rule the gate writes is sandbox-scoped; a missing --sandbox writes a GLOBAL rule.
        scoped = [l for l in body.splitlines()
                  if "policy allow network" in l or "policy deny network" in l]
        self.assertEqual(len(scoped), 2, scoped)
        for line in scoped:
            self.assertIn("--sandbox", line, line)

        # Mountless, skill-less sandboxes, removed one at a time by name.
        self.assertEqual(body.count("--skills off"), 2)
        self.assertNotIn("--mount", body)
        self.assertIn('run_sbx rm --force "$name"', body)

        # The bindings are established before any sandbox exists.
        first_create = body.index("run_sbx create")
        self.assertLess(body.index("record.py --preflight"), first_create)
        self.assertLess(body.index("record.py --inputs"), first_create)

        # On the stop path the evidence is written only after cleanup has run.
        stop = body.index("stop() {")
        stop_body = body[stop:body.index("}", body.index("record.py", stop))]
        self.assertLess(stop_body.index("cleanup"), stop_body.index("record.py"))

    def test_89_a_port_qualified_allow_on_a_permitted_host_cannot_pass(self):
        """The latent allow at port granularity. Surfaces 2-4 speak only about <host>:443 - the
        check, the connection attempt and the log all probe that port - so an allow like
        `github.com:22` is invisible to every one of them and only surface 1 can object."""
        cell = self.cell("claude", "trusted")
        for resource in ("github.com:22", "api.anthropic.com:9999", "pypi.org:8080"):
            with self.subTest(resource=resource):
                doc = self.effective_doc(cell, extra=[net_rule(cell["name"], "allow", [resource])])
                failures = self.record_module.effective_policy_failures(cell, doc)
                self.assertTrue(any(resource in f for f in failures), failures)

        # Both shapes the pinned sbx really emits stay clean: the kit's `<host>:443` and the bare
        # host `sbx policy allow network --sandbox` writes.
        self.assertEqual(self.record_module.effective_policy_failures(
            cell, self.effective_doc(cell, extra=[
                net_rule(cell["name"], "allow", ["github.com:443"])])), [])

        # And it fails the gate, while the blind surfaces still pass.
        doc = self.effective_doc(cell, extra=[net_rule(cell["name"], "allow", ["github.com:22"])])
        (self.work / f"effective-{cell['name']}.json").write_text(json.dumps(doc), encoding="utf-8")
        results = self._results(self._capture())
        self.assertEqual(results["G4.claude.trusted"], "PASS")
        self.assertEqual(results["G4.claude.trusted.exact"], "PASS")
        self.assertEqual(results["G4.claude.trusted.effective"], "FAIL")

    def test_90_a_deny_on_another_port_does_not_neutralize_a_kit_allow(self):
        """The kit's rule is editable:false and allows <host>:443, so only a deny that reaches
        that port neutralizes it."""
        cell = self.cell("claude", "untrusted")
        doc = self.effective_doc(cell, omit_denies=("claude.com",),
                                 extra=[net_rule(cell["name"], "deny", ["claude.com:8443"])])
        failures = self.record_module.effective_policy_failures(cell, doc)
        self.assertTrue(any("claude.com" in f for f in failures), failures)

    def test_91_a_matrix_that_cannot_be_derived_records_a_fail_rather_than_crashing(self):
        """A traceback out of record() would leave the PREVIOUS run's PASS - its fingerprint and
        its proven allow set - on disk, where T024 and T051 read it."""
        for malformed in ({"backends": []},
                          {"backends": {"claude": [{"host": "a.b"}], "codex": {}}},
                          {"backends": {"claude": [], "codex": []}},
                          {"backends": {"claude": {"hosts": "not-a-list"}}},
                          []):
            with self.subTest(malformed=malformed):
                path = self.tmp / "bad-inventory.json"
                path.write_text(json.dumps(malformed), encoding="utf-8")
                criteria, matrix = self.record_module.evaluate(
                    self._capture(), str(self.work),
                    inventory_file=str(path), draft_file=str(DRAFT_FILE))
                results = {row["id"]: row["result"] for row in criteria}
                self.assertEqual(results["G4.matrix"], "FAIL", results)
                self.assertIsNone(matrix)

        # End to end: the evidence document is written, as a FAIL carrying no proven set.
        status, passing = self._record_to(self._capture())
        self.assertEqual(status, "PASS")
        self.assertIn("proven_network_allowset", passing)

        original = self.record_module.cells
        self.addCleanup(setattr, self.record_module, "cells", original)

        def raises(*_args, **_kwargs):
            raise AttributeError("'list' object has no attribute 'get'")

        self.record_module.cells = raises
        status, evidence = self._record_to(self._capture())
        self.assertEqual(status, "FAIL")
        self.assertEqual({c["id"]: c["result"] for c in evidence["criteria"]}["G4.matrix"], "FAIL")
        self.assertNotIn("proven_network_allowset", evidence)
        jsonschema.Draft202012Validator(self.evidence_schema).validate(evidence)

    def test_92_the_plan_protocol_matches_the_field_positions_run_sh_reads(self):
        """plan_lines is the only coupling between the evaluator's matrix and the runner, and
        run.sh reads it by fixed awk field position, so a reorder is silent until a live run."""
        lines = self.record_module.plan_lines(self.matrix)
        script = (GATES / "G4" / "run.sh").read_text(encoding="utf-8")

        # The awk programs run.sh really uses, pinned so a reorder on either side breaks here.
        for program in ("/^cell /{print $2}",
                        '$1=="cell" && $2==n {print $5}',
                        '$1=="allow" && $2==n {print $3}',
                        '$1=="deny" && $2==n {print $3}',
                        '$1=="dest" && $2==n {print $3}'):
            self.assertIn(program, script, program)

        parsed = {}
        for line in lines:
            fields = line.split(" ")
            self.assertNotIn("", fields, line)
            verb, name = fields[0], fields[1]
            if verb == "cell":
                self.assertEqual(len(fields), 5, line)
                parsed[name] = {"backend": fields[2], "profile": fields[3], "agent": fields[4],
                                "allow": [], "deny": [], "dest": {}}
            elif verb in ("allow", "deny"):
                parsed[name][verb] = fields[2].split(",")
            elif verb == "dest":
                parsed[name]["dest"][fields[2]] = fields[3]
            else:
                self.fail(f"unknown plan verb {verb!r} in {line!r}")

        self.assertEqual(len(parsed), 4)
        for cell in self.matrix:
            got = parsed[cell["name"]]
            self.assertEqual(got["backend"], cell["backend"])
            self.assertEqual(got["profile"], cell["profile"])
            self.assertEqual(got["agent"], cell["agent"])
            self.assertEqual(got["allow"], cell["allow_rules"])
            self.assertEqual(got["deny"], cell["deny_rules"])
            self.assertEqual(got["dest"], cell["expected"])

        # `--plan` is what run.sh actually invokes, against the committed T014 documents.
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            self.assertEqual(self.record_module.main(["record.py", "--plan"]), 0)
        self.assertEqual(buffer.getvalue().splitlines(), lines)

    def test_93_an_unobserved_resource_type_is_reported_rather_than_skipped(self):
        """Rules are selected for accounting by resource_type, so that field's drift is the one
        the shape check cannot catch: a skipped rule leaves no failure and no evidence, and the
        criterion would still claim every active network rule was accounted for."""
        cell = self.cell("claude", "untrusted")

        # The three values the pinned sbx v0.43.0 really emitted, and nothing else.
        self.assertEqual(self.record_module.OBSERVED_RESOURCE_TYPES,
                         ("filesystem:read", "filesystem:write", "network"))
        self.assertEqual(self.record_module.NETWORK_RESOURCE_TYPE, "network")

        # A clean document made only of observed values still passes: the filesystem rules are
        # not network rules and are skipped, the network rules are accounted for.
        self.assertEqual(
            self.record_module.effective_policy_failures(cell, self.effective_doc(cell)), [])

        for resource_type in ("all", "Network", "network:domain", "net", ""):
            with self.subTest(resource_type=resource_type):
                rule = net_rule(cell["name"], "allow", ["exfil.example.com"])
                rule["resource_type"] = resource_type
                failures = self.record_module.effective_policy_failures(
                    cell, self.effective_doc(cell, extra=[rule]))
                self.assertTrue(any("shape drift" in f for f in failures), failures)
                self.assertTrue(any(repr(resource_type) in f for f in failures), failures)

        # And it fails the gate, while the three probe surfaces stay blind to it.
        rule = net_rule(cell["name"], "allow", ["exfil.example.com"])
        rule["resource_type"] = "all"
        (self.work / f"effective-{cell['name']}.json").write_text(
            json.dumps(self.effective_doc(cell, extra=[rule])), encoding="utf-8")
        results = self._results(self._capture())
        self.assertEqual(results["G4.claude.untrusted"], "PASS")
        self.assertEqual(results["G4.claude.untrusted.exact"], "PASS")
        self.assertEqual(results["G4.claude.untrusted.effective"], "FAIL")


if __name__ == "__main__":
    unittest.main()
