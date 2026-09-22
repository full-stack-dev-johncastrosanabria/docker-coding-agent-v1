"""Launcher Phase 3B: agent execution and host limits (tasks.md T068).

Two properties are tested here, and they are the ones that cannot be re-established later if the
code gets them wrong.

**The command form is pinned.** Every native Codex execution carries `--safety strict` and an
explicit `DOCKER_AGENT_KIT_DIR=<KIT_DIR>`. The tests attack that from the directions an attacker or
an accident actually would: a hostile value in the caller's environment, a repository that ships
`.claude/skills`, `.github/skills` and `.agents/skills`, and a task text that tries to smuggle a
flag. The Claude command is asserted to be unaffected by all of it, because "we hardened Codex" is
not the same claim as "we changed how Claude runs".

**The host stops the run, and records WHY.** Steps, retries, wall clock and tokens are counted from
the typed event stream as it arrives, not from the in-VM gate's advisory counters - a process with
sudo could rewrite those. And the three ways a run can end badly are kept distinct: a host limit is
`host-terminated` / `host-limit`, a native Docker Agent ceiling is a task outcome with
`limit_reached: native_ceiling`, and a malformed stream or abnormal exit is `blocked` (exit 11).
None of them is an infrastructure abort, because in all three cases the agent really did run.
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
CAPTURES = ROOT / "gates" / "G11" / "captures"
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
events = _load("dca_events", ROOT / "src" / "dca" / "events.py")
launcher = _load("dca_launcher", ROOT / "src" / "dca" / "launcher.py")
report = _load("dca_report", ROOT / "src" / "dca" / "report.py")
sbx_module = _load("dca_sbx", ROOT / "src" / "dca" / "sbx.py")
g4 = _load("dca_g4_record", ROOT / "gates" / "G4" / "record.py")
rules = _load("dca_eligibility_rules", ROOT / "gates" / "eligibility_rules.py")

MARKER = "docker-agent run --exec --json"


def git(repo, *args):
    proc = subprocess.run(["git", "-C", str(repo), *args], stdout=subprocess.PIPE,
                          stderr=subprocess.STDOUT, check=False)
    if proc.returncode != 0:
        raise AssertionError(f"git {' '.join(args)}: {proc.stdout.decode()}")
    return proc.stdout.decode()


def event(kind, **fields):
    return json.dumps({"type": kind, "agent_name": fields.pop("agent", "root"), **fields})


def tool_call(identifier, name="Bash", arguments=None):
    function = {"name": name}
    if arguments is not None:
        function["arguments"] = json.dumps(arguments)
    return event("tool_call", tool_call={"id": identifier, "type": "function",
                                         "function": function})


def stream(*lines, terminal=True):
    body = [event("stream_started", session_id="s"), *lines]
    if terminal:
        body.append(event("stream_stopped", session_id="s", reason="normal"))
    return "\n".join(body) + "\n"


class ExecutionCase(unittest.TestCase):
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
        self.versions["sbx"] = {"exact": "v0.43.0", "minimum": "0.43.0"}
        self.versions_path = self.dir / "versions.yaml"
        self.versions_path.write_text(json.dumps(self.versions, indent=2, sort_keys=True),
                                      encoding="utf-8")
        self.document = json.loads((FIXTURES / "all-eligible.json").read_text(encoding="utf-8"))
        self.document["backends"]["codex"]["credential_mechanism"] = "token-file-trusted-only"
        self.eligibility_path = self.dir / "eligibility.json"
        self.write_eligibility()

        self.state_dir = self.dir / "sbx"
        self.state_dir.mkdir()
        self.state = {
            "version": "sbx version: v0.43.0",
            "settings": {"ssh.agentForwardingEnabled": False, "ssh.agentSocketPath": ""},
            "policy": {"rules": [], "governance": {"active": False}},
            "sandboxes": [],
            "streams": {},
            "exec_status": {},
        }
        self.write_state()
        self.sbx = sbx_module.Sbx(binary=str(FAKE_SBX),
                                  env={"DCA_FAKE_SBX_DIR": str(self.state_dir)})
        self.home_config = self.dir / "cagent"
        self.home_config.mkdir()
        (self.home_config / "chatgpt-auth.json").write_text("{}", encoding="utf-8")
        self._home = launcher.HOST_CONFIG_DIR
        launcher.HOST_CONFIG_DIR = str(self.home_config)
        for name in launcher.PROVIDER_KEY_NAMES:
            os.environ.pop(name, None)

    def tearDown(self):
        launcher.HOST_CONFIG_DIR = self._home
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

    def record_stream(self, text, exit_status=0, name="run"):
        path = self.dir / f"{name}.jsonl"
        path.write_text(text, encoding="utf-8")
        self.state["streams"][MARKER] = str(path)
        self.state["exec_status"][MARKER] = exit_status
        self.write_state()

    def stub_kit(self, path, backend):
        os.makedirs(path, exist_ok=True)
        Path(path, "spec.yaml").write_text(f"# stub kit for {backend}\n", encoding="utf-8")
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
        self.state["create_output"] = (
            f"created from {self.versions['sandbox_bases'][backend]['base']}")
        self.write_state()
        instance = self.make(**overrides)
        instance.preconditions()
        instance.provision()
        return instance

    def agent_calls(self):
        log = self.state_dir / "calls.jsonl"
        if not log.is_file():
            return []
        return [json.loads(line)["argv"] for line in log.read_text(encoding="utf-8").splitlines()
                if MARKER in line]


# --- the command form -----------------------------------------------------------------------------


class TestCommandForm(ExecutionCase):
    def test_01_the_agent_runs_non_interactively_with_stdin_closed(self):
        command = launcher.build_agent_command("claude", "/opt/dca/agents/claude.yaml",
                                               "/run/dca/task.txt")
        self.assertIn("--exec --json", command)
        self.assertIn("</dev/null", command)

    def test_02_every_codex_execution_carries_safety_strict(self):
        command = launcher.build_agent_command("codex", "/opt/dca/agents/codex.yaml",
                                               "/run/dca/task.txt")
        self.assertIn("--safety strict", command)
        self.assertNotIn("restricted", command)
        self.assertNotIn("autonomous", command)

    def test_03_every_codex_execution_pins_the_kit_directory_explicitly(self):
        command = launcher.build_agent_command("codex", "/opt/dca/agents/codex.yaml",
                                               "/run/dca/task.txt")
        self.assertIn(f"export DOCKER_AGENT_KIT_DIR={launcher.KIT_DIR}", command)

    def test_04_the_claude_command_carries_neither(self):
        command = launcher.build_agent_command("claude", "/opt/dca/agents/claude.yaml",
                                               "/run/dca/task.txt")
        self.assertNotIn("--safety", command)
        self.assertNotIn("DOCKER_AGENT_KIT_DIR", command)

    def test_05_a_hostile_caller_environment_cannot_change_the_kit_directory(self):
        os.environ["DOCKER_AGENT_KIT_DIR"] = "/tmp/attacker-skills"
        try:
            command = launcher.build_agent_command("codex", "/opt/dca/agents/codex.yaml",
                                                   "/run/dca/task.txt")
        finally:
            os.environ.pop("DOCKER_AGENT_KIT_DIR", None)
        self.assertIn(f"export DOCKER_AGENT_KIT_DIR={launcher.KIT_DIR}", command)
        self.assertNotIn("attacker-skills", command)

    def test_06_repository_skill_directories_cannot_change_the_command(self):
        for relative in (".claude/skills/verification", ".github/skills/verification",
                         ".agents/skills/verification"):
            target = self.repo / relative
            target.mkdir(parents=True)
            (target / "SKILL.md").write_text("HOSTILE\n", encoding="utf-8")
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "--quiet", "-m", "hostile skills")
        instance = self.provisioned(backend="codex")
        self.record_stream(stream(), exit_status=0)
        instance.execute_agent(timeout=30)
        argv = self.agent_calls()
        self.assertTrue(argv)
        script = argv[0][-1]
        self.assertIn(f"export DOCKER_AGENT_KIT_DIR={launcher.KIT_DIR}", script)
        self.assertIn("--safety strict", script)
        self.assertNotIn("HOSTILE", script)

    def test_07_the_task_text_cannot_smuggle_a_flag(self):
        instance = self.provisioned(backend="codex",
                                    task="fix it --safety autonomous --skip-gates")
        self.record_stream(stream(), exit_status=0)
        instance.execute_agent(timeout=30)
        script = self.agent_calls()[0][-1]
        # The task reaches the agent through a FILE, never interpolated into the command line.
        self.assertIn("$(cat /run/dca/task.txt)", script)
        self.assertNotIn("autonomous", script)
        self.assertEqual(script.count("--safety"), 1)

    def test_08_the_trusted_codex_run_points_at_the_staged_credential_directory(self):
        instance = self.provisioned(backend="codex", trust="trusted")
        self.record_stream(stream(), exit_status=0)
        instance.execute_agent(timeout=30)
        self.assertIn(f"--config-dir {launcher.VM_CONFIG_DIR}", self.agent_calls()[0][-1])


# --- host limits ------------------------------------------------------------------------------------


class TestHostLimits(ExecutionCase):
    def test_20_a_step_limit_stops_the_run_from_the_host(self):
        body = [tool_call(f"call-{n}", arguments={"command": "make"}) for n in range(40)]
        self.record_stream(stream(*body, terminal=False), exit_status=0)
        instance = self.provisioned()
        instance.host_limits = lambda classification=None: {"steps": 5, "retries": 99,
                                                            "tokens": 10 ** 9,
                                                            "wall_clock_seconds": 60}
        analysis, host_stop, _, _ = instance.execute_agent()
        self.assertEqual(host_stop["reason"], "steps")
        self.assertEqual(analysis.limit_reached, "steps")
        self.assertEqual(analysis.run_integrity()["stream"], "host-terminated")
        self.assertEqual(analysis.run_integrity()["agent_exit"], "host-limit")

    def test_21_a_retry_limit_stops_the_run(self):
        body = []
        for n in range(6):
            body += [tool_call(f"v{n}", arguments={"command": "make test"}),
                     event("tool_call_response", tool_call_id=f"v{n}",
                           response="tests failed\nexit status 1"),
                     tool_call(f"e{n}", name="write_file",
                               arguments={"path": "src/fix.py", "content": "x"})]
        self.record_stream(stream(*body, terminal=False), exit_status=0)
        instance = self.provisioned(verify=["make test"])
        instance.host_limits = lambda classification=None: {"steps": 10 ** 6, "retries": 2,
                                                            "tokens": 10 ** 9,
                                                            "wall_clock_seconds": 60}
        analysis, host_stop, _, _ = instance.execute_agent()
        self.assertEqual(host_stop["reason"], "retries")
        self.assertEqual(analysis.limit_reached, "retries")

    def test_22_a_token_limit_stops_the_run(self):
        body = [event("token_usage", session_id="s",
                      usage={"input_tokens": 10_000 * n, "output_tokens": 0})
                for n in range(1, 20)]
        self.record_stream(stream(*body, terminal=False), exit_status=0)
        instance = self.provisioned()
        instance.host_limits = lambda classification=None: {"steps": 10 ** 6, "retries": 99,
                                                            "tokens": 25_000,
                                                            "wall_clock_seconds": 60}
        analysis, host_stop, _, _ = instance.execute_agent()
        self.assertEqual(host_stop["reason"], "tokens")

    def test_23_a_wall_clock_timer_stops_the_run(self):
        self.state["stream_delay"] = 0.05
        body = [tool_call(f"call-{n}", arguments={"command": "make"}) for n in range(200)]
        self.record_stream(stream(*body, terminal=False), exit_status=0)
        instance = self.provisioned()
        instance.host_limits = lambda classification=None: {"steps": 10 ** 6, "retries": 99,
                                                            "tokens": 10 ** 9,
                                                            "wall_clock_seconds": 1}
        analysis, host_stop, _, _ = instance.execute_agent()
        self.assertEqual(host_stop["reason"], "wall_clock")
        self.assertEqual(analysis.limit_reached, "wall_clock")

    def test_24_the_limits_in_force_follow_the_classification(self):
        instance = self.make()
        direct = instance.host_limits("direct")
        planned = instance.host_limits("planned")
        self.assertLess(direct["steps"], planned["steps"])
        self.assertLess(direct["wall_clock_seconds"], planned["wall_clock_seconds"])
        instance.classification = "planned"
        self.assertEqual(instance.host_limits(), planned)

    def test_25_a_run_inside_its_limits_is_not_stopped(self):
        self.record_stream(stream(tool_call("one", arguments={"command": "ls"})), exit_status=0)
        instance = self.provisioned()
        analysis, host_stop, _, _ = instance.execute_agent(timeout=60)
        self.assertIsNone(host_stop)
        self.assertEqual(analysis.run_integrity()["stream"], "complete")
        self.assertEqual(analysis.steps, 1)

    def test_26_the_host_counts_from_the_stream_not_from_in_vm_state(self):
        self.record_stream(stream(*[tool_call(f"c{n}") for n in range(7)]), exit_status=0)
        instance = self.provisioned()
        analysis, _, _, _ = instance.execute_agent(timeout=60)
        self.assertEqual(analysis.steps, 7)

    def test_27_the_event_stream_is_written_to_the_run_output(self):
        self.record_stream(stream(tool_call("one")), exit_status=0)
        instance = self.provisioned()
        instance.execute_agent(timeout=60)
        path = Path(instance.request.out) / "events.jsonl"
        self.assertTrue(path.is_file())
        self.assertIn("tool_call", path.read_text(encoding="utf-8"))


    def test_28_the_agents_stderr_is_kept_for_diagnosis_with_credentials_redacted(self):
        """Regression (dca bench, Codex): runs that died before any event left nothing to read."""
        jwt = "eyJhbGciOiJSUzI1NiJ9.eyJzdWIiOiJ4In0.c2lnbmF0dXJl"
        self.state["exec_stderr"] = (f"error: model request failed: 401\n"
                                     f"Authorization: Bearer {jwt}\nkey sk-proj-abcdef123456\n")
        self.record_stream(stream(terminal=False), exit_status=1)
        instance = self.provisioned()
        instance.execute_agent(timeout=60)
        text = (Path(instance.request.out) / "agent.stderr.txt").read_text(encoding="utf-8")
        self.assertIn("model request failed: 401", text)
        self.assertNotIn(jwt, text)
        self.assertNotIn("sk-proj-abcdef123456", text)

    def test_29_agent_stderr_is_capped(self):
        self.state["exec_stderr"] = "x" * 200000 + "\nlast line\n"
        self.record_stream(stream(tool_call("one")), exit_status=0)
        instance = self.provisioned()
        instance.execute_agent(timeout=60)
        text = (Path(instance.request.out) / "agent.stderr.txt").read_text(encoding="utf-8")
        self.assertLessEqual(len(text), launcher.AGENT_STDERR_LIMIT + 200)
        self.assertTrue(text.rstrip().endswith("last line"))


# --- the three bad endings, kept distinct --------------------------------------------------------


class TestEndings(ExecutionCase):
    def native_ceiling_stream(self):
        return stream(tool_call("one", arguments={"command": "make"}),
                      event("budget_exceeded", budget="max_tokens", used=120000, max=100000,
                            config_path="budget.max_tokens"),
                      terminal=False)

    def test_30_a_native_ceiling_is_a_task_outcome_not_an_abort(self):
        self.record_stream(self.native_ceiling_stream(), exit_status=1)
        instance = self.provisioned()
        analysis, host_stop, _, _ = instance.execute_agent(timeout=60)
        self.assertIsNone(host_stop)
        self.assertEqual(analysis.limit_reached, "native_ceiling")
        self.assertEqual(analysis.run_integrity()["stream"], "complete")
        self.assertEqual(analysis.run_integrity()["agent_exit"], "normal")
        self.assertEqual(analysis.native_ceiling["config_path"], "budget.max_tokens")

    def test_31_a_native_ceiling_finalizes_as_blocked_with_a_written_report(self):
        self.record_stream(self.native_ceiling_stream(), exit_status=1)
        instance = self.provisioned()
        analysis, _, _, _ = instance.execute_agent(timeout=60)
        final = report.build(
            run_id=instance.request.run_id, backend="claude", trust_level="trusted",
            task_fingerprint=instance.request.task_fingerprint,
            source={"ref": instance.source_ref, "commit": instance.source_commit,
                    "bundle_sha256": instance.bundle_sha256, "uncommitted_ignored": False},
            sandbox_settings=instance.sandbox_settings, analysis=analysis, agent_report=None,
            change_set={"branch": None, "base_commit": instance.source_commit,
                        "head_commit": None, "files": []},
            limits_configured=instance.host_limits(), versions=instance.version_block())
        report.validate(final)
        self.assertEqual(final["final_outcome"], "blocked")
        self.assertEqual(report.exit_status(final), 11)
        self.assertEqual(final["limits"]["limit_reached"], "native_ceiling")

    def test_32_an_abnormal_agent_exit_is_blocked_not_an_abort(self):
        self.record_stream(stream(tool_call("one"), terminal=False), exit_status=137)
        instance = self.provisioned()
        analysis, host_stop, exit_status, _ = instance.execute_agent(timeout=60)
        self.assertIsNone(host_stop)
        self.assertEqual(exit_status, 137)
        self.assertEqual(analysis.run_integrity()["agent_exit"], "abnormal")

    def test_33_a_malformed_stream_is_blocked_not_an_abort(self):
        self.record_stream("this is not an event stream\n", exit_status=0)
        instance = self.provisioned()
        analysis, _, _, _ = instance.execute_agent(timeout=60)
        self.assertEqual(analysis.run_integrity()["stream"], "malformed")
        self.assertFalse(analysis.ok)

    def test_34_the_real_captures_still_read_the_same_way_through_the_launcher(self):
        available = [name for name, entry in
                     json.loads((ROOT / "gates" / "eligibility.json").read_text(
                         encoding="utf-8"))["backends"].items() if entry.get("available")]
        for backend in available:
            with self.subTest(backend=backend):
                self.record_stream(
                    (CAPTURES / backend / "count.jsonl").read_text(encoding="utf-8"),
                    exit_status=0, name=f"capture-{backend}")
                instance = self.provisioned()
                analysis, _, _, _ = instance.execute_agent(timeout=60)
                self.assertEqual(analysis.steps, 3)
                self.assertEqual(analysis.run_integrity()["stream"], "complete")


if __name__ == "__main__":
    unittest.main()
