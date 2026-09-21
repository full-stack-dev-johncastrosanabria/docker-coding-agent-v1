"""The `dca run` launcher: preconditions, provisioning, execution, finalization (T064–T071).

Standard library only. This is the **host-side enforcement point**. Everything the system actually
guarantees is decided here, because nothing inside the VM can be trusted to decide it:

  * which backend may run at all, from host gate evidence only (`gates/eligibility.json`);
  * what source reaches the VM - the committed state of one local branch, as a bundle, with no
    host mount;
  * what the sandbox may reach on the network, applied per sandbox from `network.yaml`;
  * how long the run may go on, counted from the typed event stream and stopped by the host;
  * what comes back - a bundle validated in quarantine before the developer's repository is
    touched at all;
  * what the run is finally said to have achieved, recomputed rather than taken from the agent.

THE PHASES ARE ORDERED AND THE ORDER IS LOAD-BEARING. Phase 1 refuses (exit 3) before anything
exists. Phase 2 produces a `blocked` report with `sandbox_created: false` - a real disposition,
not an error - for a request that is valid but must not run. Only then does Phase 3 create
anything. A failure inside Phase 3 is an infrastructure abort (exit 4) with no report at all,
because Docker or git failing says nothing about the developer's code.

THE CODEX COMMAND IS BUILT IN ONE PLACE, `build_agent_command`, and it always carries
`--safety strict` and an explicit `DOCKER_AGENT_KIT_DIR=<KIT_DIR>`. Both come from constants here,
never from the caller's environment, the task text or repository content, and `dca` exposes no
option to change either. G11 part B's harness calls this same builder, so the gate cannot
accidentally prove a command form that production does not use.
"""

import datetime
import json
import os
import re
import secrets
import shutil
import subprocess
import tempfile
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.dirname(os.path.dirname(_HERE))


def _sideload(name, filename):
    import importlib.util
    import sys

    module = sys.modules.get(name)
    if module is None:
        spec = importlib.util.spec_from_file_location(name, os.path.join(_HERE, filename))
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
    return module


try:
    from . import eligibility as eligibility_module
    from . import events as events_module
    from . import fingerprint as fingerprint_module
    from . import grants as grants_module
    from . import report as report_module
    from . import source as source_module
    from . import taskid as taskid_module
    from .errors import InfraAbort, PreconditionError, UsageError
    from .sbx import Sbx, SbxError
except ImportError:  # loaded by path in tests
    eligibility_module = _sideload("dca_eligibility", "eligibility.py")
    events_module = _sideload("dca_events", "events.py")
    fingerprint_module = _sideload("dca_fingerprint", "fingerprint.py")
    grants_module = _sideload("dca_grants", "grants.py")
    report_module = _sideload("dca_report", "report.py")
    source_module = _sideload("dca_source", "source.py")
    taskid_module = _sideload("dca_taskid", "taskid.py")
    _errors = _sideload("dca_errors", "errors.py")
    InfraAbort, PreconditionError, UsageError = (
        _errors.InfraAbort, _errors.PreconditionError, _errors.UsageError)
    _sbx = _sideload("dca_sbx", "sbx.py")
    Sbx, SbxError = _sbx.Sbx, _sbx.SbxError

# --- fixed in-VM geography -------------------------------------------------------------------

KIT_DIR = "/opt/dca"
AGENT_BIN = "/opt/dca/bin/docker-agent"
WORKSPACE = "/workspace"
RUN_DIR = "/run/dca"
SCRATCH_DIR = "/run/dca/out"
STATE_DIR = "/run/dca/state"
#: Where the Codex trusted token-file mechanism stages the ONE credential file, when it applies.
VM_CONFIG_DIR = "/run/dca/cagent"
CREDENTIAL_FILE = "chatgpt-auth.json"
HOST_CONFIG_DIR = os.path.expanduser("~/.config/cagent")

#: The sbx template name per backend. The exact base image and digest are pinned in
#: runtime/versions.yaml and checked against what sbx resolved; the launcher never substitutes one.
BACKEND_TEMPLATE = {"claude": "claude", "codex": "docker-agent"}
BACKENDS = ("claude", "codex")
TRUST_LEVELS = ("trusted", "untrusted")

#: Provider API-key variables, checked for PRESENCE BY NAME ONLY. No value is ever read.
PROVIDER_KEY_NAMES = ("ANTHROPIC_API_KEY", "OPENAI_API_KEY")

MIN_SBX = (0, 43, 0)

RUN_ID = re.compile(r"^run-[0-9TZ-]+-[0-9a-f]{6}$")


def new_run_id(now=None):
    stamp = (now or datetime.datetime.now(datetime.timezone.utc)).strftime("%Y-%m-%dT%H-%M-%SZ")
    return f"run-{stamp}-{secrets.token_hex(3)}"


def _version_tuple(text):
    match = re.search(r"(\d+)\.(\d+)\.(\d+)", text or "")
    return tuple(int(part) for part in match.groups()) if match else None


# --- the request ---------------------------------------------------------------------------------


class RunRequest:
    """One `dca run` invocation, already parsed. The CLI owns parsing; this owns meaning."""

    def __init__(self, repo, task, ref=None, backend="claude", trust="untrusted", criteria=None,
                 verify=(), approve=(), approval_report=None, ignore_uncommitted=False,
                 out=None, allow_drift=False, run_id=None):
        if backend not in BACKENDS:
            raise UsageError(f"--backend must be one of {BACKENDS}, got {backend!r}")
        if trust not in TRUST_LEVELS:
            raise UsageError(f"--trust must be one of {TRUST_LEVELS}, got {trust!r}")
        self.repo = os.path.abspath(str(repo))
        self.task = task
        self.ref = ref
        self.backend = backend
        # FR-029a: absence of --trust is untrusted. The CLI passes the default through rather than
        # leaving it None, so there is exactly one place the default lives.
        self.trust = trust
        self.criteria = list(criteria or [])
        self.verify = list(verify)
        self.approve = list(approve)
        self.approval_report = approval_report
        self.ignore_uncommitted = bool(ignore_uncommitted)
        self.allow_drift = bool(allow_drift)
        self.run_id = run_id or new_run_id()
        self.out = os.path.abspath(str(out)) if out else os.path.join(
            os.path.dirname(self.repo), ".dca-runs", self.run_id)
        self.task_fingerprint = taskid_module.task_fingerprint(task, self.criteria, self.verify)


# --- the execution primitive ----------------------------------------------------------------------


def build_agent_command(backend, config_path, task_path, kit_dir=KIT_DIR, config_dir=None,
                        workspace=WORKSPACE, agent_bin=AGENT_BIN):
    """The exact in-VM shell command for one native Docker Agent execution.

    ONE builder, used by `dca run` and by G11 part B's harness, because the gate has to prove the
    command production actually issues.

    For Codex it always contains:
      * `--safety strict`, which outranks user-level Docker Agent settings. There is no code path
        that omits it and no option that changes it;
      * `DOCKER_AGENT_KIT_DIR=<kit_dir>`, exported in the command itself from the trusted staged-kit
        root. It is never read from the caller's environment or derived from repository content, so
        a repository that ships `.claude/skills`, `.github/skills` or `.agents/skills` cannot
        replace a runtime skill (research R17, E18).

    Stdin is redirected from /dev/null: `--exec --json` must be non-interactive, and a run that
    could block on a prompt would hang rather than fail.
    """
    if backend not in BACKENDS:
        raise UsageError(f"unknown backend {backend!r}")
    parts = [f"cd {workspace}"]
    flags = ""
    if backend == "codex":
        # `export` before `exec`, not `VAR=x exec cmd`: the assignment has to reach the process
        # Docker Agent becomes, and this form says so unambiguously in every POSIX shell.
        parts.append(f"export DOCKER_AGENT_KIT_DIR={kit_dir}")
        flags = " --safety strict"
        if config_dir:
            flags += f" --config-dir {config_dir}"
    parts.append(f"exec {agent_bin} run --exec --json{flags} "
                 f"{config_path} \"$(cat {task_path})\" </dev/null")
    return " && ".join(parts)


# --- the launcher ----------------------------------------------------------------------------------


class Launcher:
    """Phases 1-3 of contracts/launcher-cli.md, in order."""

    def __init__(self, request, sbx=None, repo_root=None, eligibility_path=None,
                 versions_path=None, artifact=None, clock=time.monotonic, kit_builder=None):
        self.request = request
        self.repo_root = repo_root or _REPO_ROOT
        self.sbx = sbx if sbx is not None else Sbx()
        self.eligibility_path = eligibility_path or os.path.join(
            self.repo_root, "gates", "eligibility.json")
        self.versions_path = versions_path or os.path.join(
            self.repo_root, "runtime", "versions.yaml")
        self.artifact = artifact or os.environ.get("DCA_DOCKER_AGENT_ARTIFACT")
        self.clock = clock
        # Production always builds the real kit. The seam exists so an integration test can skip
        # copying a 131 MB pinned binary it is not testing; the kit's own contents are proven by
        # the parity tests and by `sbx kit validate` in T062.
        self.kit_builder = kit_builder

        self.evidence = None
        self.versions = None
        self.source_ref = None
        self.source_commit = None
        self.dirty_paths = []
        self.bundle_sha256 = None
        self.sandbox = None
        self.sandbox_settings = None
        self.grants = {"grants": []}
        self.limits = None
        self.classification = "direct"
        self.workdir = None
        self.kit_dir = None

    # --- phase 1: preconditions -----------------------------------------------------------

    def preconditions(self):
        """Every check in contracts/launcher-cli.md Phase 1. Any failure is exit 3, no report."""
        request = self.request

        # 1. repository and a packageable named reference.
        source_module.require_repository_root(request.repo)
        self.source_ref, self.source_commit = source_module.resolve_branch(request.repo,
                                                                          request.ref)
        # 2. dirty checkout.
        self.dirty_paths = source_module.check_clean(request.repo, request.ignore_uncommitted)

        # 3. provider API-key contamination, by NAME only.
        present = [name for name in PROVIDER_KEY_NAMES if name in os.environ]
        if present:
            raise PreconditionError(
                "these provider API-key variables are present in the environment and dca is "
                f"subscription-only: {', '.join(sorted(present))}. Unset them and re-run. "
                "(No value was read.)")

        # 7 (partly first): the gate evidence, because several later checks read it. Every
        # EvidenceProblem is a precondition failure - exit 3, no report - so the translation
        # happens once, here, rather than in each check.
        try:
            self.versions = eligibility_module.load_versions(self.versions_path)
            self.evidence = eligibility_module.load(self.eligibility_path)
            eligibility_module.check_freshness(self.evidence, self.versions,
                                               self.eligibility_path)
            eligibility_module.check_common_gates(self.evidence)
            eligibility_module.check_backend_runnable(self.evidence, request.backend)
        except eligibility_module.EvidenceProblem as exc:
            raise PreconditionError(str(exc)) from exc
        except (OSError, ValueError) as exc:
            raise PreconditionError(
                f"runtime/versions.yaml could not be read: {exc}") from exc

        # 4. sbx availability, version and the selected backend's sandbox base pin.
        self._check_sbx_version()
        base = (self.versions.get("sandbox_bases") or {}).get(request.backend) or {}
        # Presence of BOTH fields is the requirement: an image reference without a version pins
        # a moving tag, and a version without a reference names nothing. The digest FORMAT of the
        # real pins is checked by scripts/verify.sh, which reads the real file.
        if not base.get("base") or not base.get("version"):
            raise PreconditionError(
                f"runtime/versions.yaml has no pinned sandbox base for {request.backend!r}. "
                "This is refused even with --allow-drift: the launcher never resolves or "
                "substitutes a base at runtime.")

        # 5. SSH agent.
        self._check_ssh()

        # 6. backend authentication, from safe status fields only.
        self._check_backend_auth()

        # 9. global network-policy drift.
        self._check_network_fingerprint()

        # 8. approvals, last, because provenance needs the resolved source commit.
        self.grants = self._resolve_grants()
        return True

    def _check_sbx_version(self):
        try:
            reported = self.sbx.version()
        except (SbxError, OSError) as exc:
            raise PreconditionError(
                f"sbx is not available: {exc}. Install Docker Sandboxes "
                f">= {'.'.join(map(str, MIN_SBX))} and re-run.") from exc
        observed = _version_tuple(reported)
        if observed is None:
            raise PreconditionError(f"sbx did not report a usable version: {reported!r}")
        if observed < MIN_SBX:
            raise PreconditionError(
                f"sbx {'.'.join(map(str, observed))} is below the required "
                f"{'.'.join(map(str, MIN_SBX))} (mountless create and --skills=off)")
        exact = (self.versions.get("sbx") or {}).get("exact")
        if exact and exact.lstrip("v") != ".".join(map(str, observed)):
            if not self.request.allow_drift:
                raise PreconditionError(
                    f"sbx reports {reported!r} but runtime/versions.yaml pins {exact}. "
                    "Re-run with --allow-drift to proceed (the run is then not "
                    "acceptance-eligible).")

    def _check_ssh(self):
        """Precondition 5. A setting that cannot be read is refused, never read as 'no agent'."""
        try:
            settings = self.sbx.settings()
        except (SbxError, OSError) as exc:
            raise PreconditionError(
                f"the sbx settings could not be read, so SSH-agent state is unknown: {exc}") \
                from exc
        forwarding = settings.get("ssh.agentForwardingEnabled")
        socket_path = settings.get("ssh.agentSocketPath")
        if forwarding is True:
            raise PreconditionError(
                "sbx ssh.agentForwardingEnabled is true. dca never changes global sbx settings; "
                "run `sbx settings set ssh.agentForwardingEnabled false` yourself and re-run.")
        if forwarding is None:
            raise PreconditionError(
                "sbx did not report ssh.agentForwardingEnabled, so it cannot be read as false")
        if socket_path:
            raise PreconditionError(
                "sbx ssh.agentSocketPath is configured. V1 refuses a configured host agent socket "
                "as a deliberately strict baseline; clear it and re-run.")

    def _check_backend_auth(self):
        backend = self.request.backend
        if backend == "codex":
            credential = os.path.join(HOST_CONFIG_DIR, CREDENTIAL_FILE)
            if not os.path.isfile(credential) or os.path.getsize(credential) == 0:
                raise PreconditionError(
                    "no ChatGPT sign-in is present for the codex backend. Sign in with "
                    "`docker agent login chatgpt` and re-run. (No token value was read.)")
        else:
            status = shutil.which("claude")
            if status is None:
                raise PreconditionError(
                    "the claude CLI is not installed, so Claude authentication is unavailable. "
                    "You can re-run with --backend codex; dca never switches backends by itself.")

    def _check_network_fingerprint(self):
        expected = eligibility_module.network_policy_fingerprint(self.evidence)
        actual = self.current_network_fingerprint()
        if actual != expected:
            raise PreconditionError(
                "the global network policy has changed since the gates ran: it now fingerprints "
                f"as {actual}, and gates/eligibility.json recorded {expected}. Re-run G4 and "
                "production conformance. dca never repairs or mutates global sbx settings.")

    def current_network_fingerprint(self):
        """Recomputed read-only, with the same canonical algorithm G4 recorded."""
        g4 = _sideload_path("dca_g4_record",
                            os.path.join(self.repo_root, "gates", "G4", "record.py"))
        sandboxes = self.sbx.list_sandboxes()
        if sandboxes:
            raise PreconditionError(
                f"{len(sandboxes)} sandbox(es) already exist, so the global network-policy "
                "fingerprint cannot be read (a sandbox-scoped rule changes how the global rules "
                "enumerate). Remove them and re-run.")
        policy = self.sbx.policy()
        governance = bool((policy.get("governance") or {}).get("active"))
        return g4.fingerprint(policy, governance)

    def _resolve_grants(self):
        """Grants are MANUFACTURED on the host from the authoritative prior report, never read.

        A failure here is a stale or undiscoverable approval: exit 3, with the same remedy every
        time - re-run without --approve to obtain a fresh request.
        """
        if not self.request.approve:
            return {"run_id": self.request.run_id, "granted_by": grants_module.GRANTED_BY,
                    "grants": []}
        try:
            return grants_module.create_grants(
                request_ids=self.request.approve,
                new_run={"run_id": self.request.run_id,
                         "task_fingerprint": self.request.task_fingerprint,
                         "source_commit": self.source_commit,
                         "backend": self.request.backend,
                         "trust_level": self.request.trust},
                repo_root=self.request.repo,
                approval_report=self.request.approval_report)
        except grants_module.GrantError as exc:
            raise PreconditionError(
                f"the approval is stale or undiscoverable: {exc}. Re-run without --approve to "
                "obtain a new request.") from exc

    def prepare_gate_run(self):
        """Resolve source and load evidence for the G11 part-B harness, WITHOUT precondition 7.

        This exists because of a genuine ordering problem: `dca run` refuses a backend whose G11 is
        not a final PASS, and the gate that MAKES it a final PASS has to execute on that backend.
        So the harness checks its own prerequisites - part A PASS, production conformance PASS,
        common and backend gates PASS - and then calls this to set up the same Phase 3 the launcher
        uses.

        It is NOT a bypass. It is unreachable from `dca run` and from `dca.cli`: nothing in the CLI
        calls it, and there is no flag that reaches it. Everything it skips is checked by the
        harness instead, and everything it keeps - the source rules, the dirty-checkout rule, the
        provider-key rule, sbx and SSH state, the network fingerprint - still applies, because a
        gate run that ignored those would be proving a property about a different environment.
        """
        request = self.request
        source_module.require_repository_root(request.repo)
        self.source_ref, self.source_commit = source_module.resolve_branch(request.repo,
                                                                          request.ref)
        self.dirty_paths = source_module.check_clean(request.repo, request.ignore_uncommitted)
        present = [name for name in PROVIDER_KEY_NAMES if name in os.environ]
        if present:
            raise PreconditionError(
                "these provider API-key variables are present and dca is subscription-only: "
                + ", ".join(sorted(present)))
        try:
            self.versions = eligibility_module.load_versions(self.versions_path)
            self.evidence = eligibility_module.load(self.eligibility_path)
            eligibility_module.check_freshness(self.evidence, self.versions,
                                               self.eligibility_path)
            eligibility_module.check_common_gates(self.evidence)
            eligibility_module.check_backend_available(self.evidence, request.backend)
        except eligibility_module.EvidenceProblem as exc:
            raise PreconditionError(str(exc)) from exc
        self._check_sbx_version()
        self._check_ssh()
        self._check_backend_auth()
        self._check_network_fingerprint()
        return True

    # --- phase 2: policy disposition --------------------------------------------------------

    def policy_disposition(self):
        """A `blocked` report with `sandbox_created: false`, or None to proceed to Phase 3."""
        if self.request.trust != "untrusted":
            return None
        try:
            missing = eligibility_module.untrusted_blockers(self.evidence, self.request.backend)
        except eligibility_module.EvidenceProblem as exc:
            raise PreconditionError(str(exc)) from exc
        if not missing:
            return None
        reason = (f"an untrusted run on the {self.request.backend} backend requires "
                  f"{', '.join(missing)}, which have not passed")
        return self.blocked_report(
            reason,
            "re-run with --trust trusted for a repository you trust, choose a backend whose "
            f"untrusted gates pass, or complete {', '.join(missing)}")

    def blocked_report(self, reason, action):
        analysis = events_module.analyze("", sandbox_created=False)
        return report_module.build(
            run_id=self.request.run_id, backend=self.request.backend,
            trust_level=self.request.trust, task_fingerprint=self.request.task_fingerprint,
            source={"ref": self.source_ref, "commit": self.source_commit,
                    "bundle_sha256": None,
                    "uncommitted_ignored": self.request.ignore_uncommitted,
                    "dirty_paths": list(self.dirty_paths)},
            sandbox_settings=None, analysis=analysis, agent_report=None,
            change_set={"branch": None, "base_commit": self.source_commit, "head_commit": None,
                        "files": []},
            limits_configured=self.host_limits(), versions=self.version_block(),
            approvals=[],
            primary_reason=reason, human_action_required=action)

    # --- shared helpers ------------------------------------------------------------------------

    def host_limits(self, classification=None):
        with open(os.path.join(self.repo_root, "runtime", "policy", "limits.yaml"),
                  encoding="utf-8") as handle:
            limits = json.load(handle)
        return dict(limits["host_limits"][classification or self.classification])

    def version_block(self):
        versions = self.versions or eligibility_module.load_versions(self.versions_path)
        return {
            "docker_agent": versions.get("docker_agent"),
            "claude_code": (versions.get("claude_code") or {}).get("exact"),
            "sbx": (versions.get("sbx") or {}).get("exact"),
            "drift": bool(self.request.allow_drift),
        }

    def network_cell(self):
        with open(os.path.join(self.repo_root, "runtime", "policy", "network.yaml"),
                  encoding="utf-8") as handle:
            network = json.load(handle)
        cell = network["backends"][self.request.backend][self.request.trust]
        allow = list(dict.fromkeys(list(cell["control_plane"]) + list(cell["allow"])
                                   + self.granted_hosts()))
        return allow, list(cell["deny"])

    def granted_hosts(self):
        """Network destinations that came from a HOST-authoritative grant, never from the VM.

        The grants document is the host copy. The in-VM copy is only a hint for the cooperative
        gate; what actually opens a destination is this list reaching the sbx policy from here.
        """
        hosts = []
        for grant in (self.grants or {}).get("grants", []):
            scope = grant.get("scope") or {}
            targets = [scope["exact"]] if isinstance(scope.get("exact"), str) else list(
                (scope.get("equivalence_class") or {}).get("targets") or [])
            for target in targets:
                if isinstance(target, str) and "." in target and "/" not in target:
                    hosts.append(target)
        return hosts


    # --- phase 3A: provisioning ---------------------------------------------------------------

    def provision(self):
        """Source bundle, sandbox, network policy, delivery, preflight. Any failure is exit 4.

        The ORDER is the contract's: the bundle exists before any sandbox does, so a source failure
        never leaves a VM behind; the network policy is applied before anything is copied in, so
        the sandbox is never briefly reachable on a wider policy than the run is entitled to.
        """
        request = self.request
        self.workdir = tempfile.mkdtemp(prefix=f"dca-{request.run_id}-")
        bundle = os.path.join(self.workdir, "src.bundle")

        # Step 1: the source bundle, from the NAMED ref. Failure here creates no sandbox at all.
        self.bundle_sha256 = source_module.create_source_bundle(
            request.repo, self.source_ref, self.source_commit, bundle)

        kit = os.path.join(self.workdir, "kit")
        try:
            if self.kit_builder is not None:
                self.kit_dir = self.kit_builder(kit, request.backend)
            else:
                stage = _sideload_path("dca_stage", os.path.join(
                    self.repo_root, "runtime", "sandbox", "kit", "stage.py"))
                self.kit_dir = stage.build_sbx_kit(
                    kit, backends=[request.backend], artifact=self.artifact,
                    eligibility_path=self.eligibility_path)
        except Exception as exc:
            raise InfraAbort(f"the sandbox kit could not be built: {exc}") from exc

        self.sandbox = f"dca-{request.run_id}"
        template = BACKEND_TEMPLATE[request.backend]
        try:
            # Step 2: a MOUNTLESS sandbox with shared skills off, from the pinned base.
            created = self.sbx.create(template, self.sandbox, self.kit_dir)
            self._check_resolved_base(created)

            # Step 3: the strict per-sandbox network policy, plus host-authoritative grants.
            allow, deny = self.network_cell()
            self.sbx.allow_network(self.sandbox, allow)
            self.sbx.deny_network(self.sandbox, deny)

            # Step 4: deliver the bundle, the run config and the grants copy.
            self._deliver(bundle)
            self._stage_codex_credential()

            # Step 5: the in-VM clone, the task branch, and the kit/gate preflight.
            self._clone_and_preflight()
        except SbxError as exc:
            raise InfraAbort(f"provisioning failed: {exc}") from exc

        allow, deny = self.network_cell()
        self.sandbox_settings = {
            "mountless": True,
            "shared_skills": "off",
            "ssh_agent_forwarding": False,
            "network_policy_digest": _digest_of({"allow": sorted(allow), "deny": sorted(deny)}),
        }
        return self.sandbox

    def _check_resolved_base(self, created_output):
        """The base sbx resolved must be the exact pinned one. Never substituted, never guessed."""
        pinned = ((self.versions.get("sandbox_bases") or {}).get(self.request.backend) or {})
        reference = pinned.get("base")
        if reference and reference not in (created_output or ""):
            # sbx does not always echo the reference; the template store is then the authority.
            try:
                templates = self.sbx.templates()
            except (SbxError, OSError):
                templates = None
            if templates is not None and reference and not json.dumps(templates).count(
                    reference.rsplit(":", 1)[0]):
                raise InfraAbort(
                    f"the sandbox was not created from the pinned base {reference}")

    def _deliver(self, bundle):
        request = self.request
        run_record = {
            "run_id": request.run_id,
            "trust_level": request.trust,
            "backend": request.backend,
            "classification": self.classification,
            "workspace": WORKSPACE,
            "scratch": SCRATCH_DIR,
            "state": STATE_DIR,
            "verification_commands": list(request.verify),
            "step_limit": self.host_limits().get("steps"),
            "steps_used": 0,
        }
        staging = os.path.join(self.workdir, "deliver")
        os.makedirs(staging, exist_ok=True)
        _write_json(os.path.join(staging, "run.json"), run_record)
        _write_json(os.path.join(staging, "grants.json"), self.grants)
        with open(os.path.join(staging, "task.txt"), "w", encoding="utf-8") as handle:
            handle.write(self._task_text())

        self.sbx.copy_in(bundle, self.sandbox, "/tmp/dca-src.bundle")
        self.sbx.copy_in(staging, self.sandbox, "/tmp/dca-run")
        self.sbx.execute(self.sandbox, (
            "set -eu\n"
            f"sudo install -d -m 0755 {RUN_DIR}\n"
            f"sudo install -d -m 0777 {SCRATCH_DIR} {STATE_DIR}\n"
            f"sudo install -m 0444 /tmp/dca-run/run.json {RUN_DIR}/run.json\n"
            f"sudo install -m 0444 /tmp/dca-run/grants.json {RUN_DIR}/grants.json\n"
            f"sudo install -m 0444 /tmp/dca-run/task.txt {RUN_DIR}/task.txt\n"
            "rm -rf /tmp/dca-run\n"))

    def _task_text(self):
        request = self.request
        lines = [request.task.rstrip("\n"), ""]
        if request.criteria:
            lines.append("Acceptance criteria:")
            lines += [f"- {item}" for item in request.criteria]
            lines.append("")
        if request.verify:
            lines.append("Required verification commands:")
            lines += [f"- {item}" for item in request.verify]
            lines.append("")
        return "\n".join(lines)

    def _stage_codex_credential(self):
        """The Codex trusted token-file mechanism: ONE file, owner-only, trusted runs only.

        The whole host config directory is never copied. An untrusted request cannot reach this
        code at all - Phase 2 blocks untrusted Codex under this mechanism - and neither the file's
        contents nor any hash of it is ever logged, reported or written to an event file.
        """
        request = self.request
        if request.backend != "codex" or request.trust != "trusted":
            return
        mechanism = eligibility_module.credential_mechanism(self.evidence, "codex")
        if mechanism != "token-file-trusted-only":
            return
        source_path = os.path.join(HOST_CONFIG_DIR, CREDENTIAL_FILE)
        if not os.path.isfile(source_path):
            raise InfraAbort(f"the ChatGPT sign-in file is missing: {CREDENTIAL_FILE}")
        staging = os.path.join(self.workdir, "cred")
        if os.path.isdir(staging):
            shutil.rmtree(staging)
        os.makedirs(staging, mode=0o700)
        shutil.copyfile(source_path, os.path.join(staging, CREDENTIAL_FILE))
        os.chmod(os.path.join(staging, CREDENTIAL_FILE), 0o600)
        self.sbx.copy_in(staging, self.sandbox, VM_CONFIG_DIR)
        self.sbx.execute(self.sandbox, (
            f"sudo chown -R \"$(id -un):$(id -gn)\" {VM_CONFIG_DIR} && "
            f"chmod 700 {VM_CONFIG_DIR} && chmod 600 {VM_CONFIG_DIR}/{CREDENTIAL_FILE}"))
        shutil.rmtree(staging, ignore_errors=True)

    def _clone_and_preflight(self):
        branch = f"dca/{self.request.run_id}"
        self.sbx.execute(self.sandbox, (
            "set -eu\n"
            f"sudo install -d -m 0755 -o \"$(id -un)\" -g \"$(id -gn)\" {WORKSPACE}\n"
            f"git clone --quiet --branch {_shell_quote(_short_ref(self.source_ref))} "
            f"/tmp/dca-src.bundle {WORKSPACE}\n"
            f"cd {WORKSPACE}\n"
            "git config user.email dca@localhost\n"
            "git config user.name 'docker coding agent'\n"
            f"test \"$(git rev-parse HEAD)\" = {self.source_commit}\n"
            f"git checkout --quiet -b {_shell_quote(branch)}\n"
            "rm -f /tmp/dca-src.bundle\n"))
        status, out, err, _ = self.sbx.execute(self.sandbox, (
            f"/usr/bin/python3 -I {KIT_DIR}/lib/dca/kit_preflight.py {KIT_DIR} "
            f"{'/home/agent/.config/cagent/config.yaml' if self.request.backend == 'codex' else '/dev/null'}"
        ), check=False)
        if status != 0:
            raise InfraAbort(f"the in-VM kit/gate preflight failed: {(err or out).strip()[:400]}")

    # --- phase 3B: execution and host limits ----------------------------------------------------

    def execute_agent(self, timeout=None):
        """Run the agent, enforcing the host limits from the typed event stream as it arrives.

        The host is the authority on limits, so it counts the events itself and stops the run. The
        in-VM gate's advisory counters exist only for a clean early stop; a process with sudo could
        rewrite them, which is exactly why they are not what this reads.
        """
        limits = self.host_limits()
        deadline = (timeout if timeout is not None
                    else limits.get("wall_clock_seconds", 1200))
        config = f"{KIT_DIR}/agents/{self.request.backend}.yaml"
        config_dir = VM_CONFIG_DIR if (
            self.request.backend == "codex"
            and self.request.trust == "trusted"
            and eligibility_module.credential_mechanism(self.evidence, "codex")
            == "token-file-trusted-only") else None
        script = build_agent_command(self.request.backend, config, f"{RUN_DIR}/task.txt",
                                     config_dir=config_dir)

        argv = [self.sbx.binary, "exec", self.sandbox, "sh", "-c", script]
        # The wrapper's environment, not a fresh copy of os.environ: SSH_AUTH_SOCK removal (and
        # anything else the wrapper pins) has to apply to the streamed execution too.
        environment = self.sbx.environment()
        started = self.clock()
        proc = subprocess.Popen(argv, env=environment, stdin=subprocess.DEVNULL,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        self.sbx.calls.append({"argv": argv, "status": None, "streaming": True})

        accountant = events_module.StreamAccountant(self.request.verify)
        host_stop = None
        captured = []
        events_path = os.path.join(self.request.out, "events.jsonl")
        os.makedirs(self.request.out, exist_ok=True)
        with open(events_path, "w", encoding="utf-8") as sink:
            for line in proc.stdout:
                text = line.decode("utf-8", "replace")
                captured.append(text)
                sink.write(text)
                accountant.feed(text)
                if host_stop is None:
                    reason = accountant.limit_reached(limits)
                    if reason is not None:
                        host_stop = {"reason": reason, "at_step": accountant.steps}
                        proc.kill()
                        break
                if host_stop is None and deadline and self.clock() - started >= deadline:
                    host_stop = {"reason": "wall_clock", "at_step": accountant.steps}
                    proc.kill()
                    break
            if host_stop is not None:
                for line in proc.stdout:
                    text = line.decode("utf-8", "replace")
                    captured.append(text)
                    sink.write(text)
        stderr = proc.stderr.read().decode("utf-8", "replace")
        exit_status = proc.wait()
        if host_stop is None and deadline and self.clock() - started >= deadline:
            host_stop = {"reason": "wall_clock", "at_step": accountant.steps}

        analysis = events_module.analyze(
            "".join(captured),
            exit_status=None if host_stop else exit_status,
            host_stop=host_stop, sandbox_created=True,
            verification_commands=self.request.verify)
        return analysis, host_stop, exit_status, stderr

    # --- phase 3C: retrieval, finalization, cleanup ---------------------------------------------

    def final_verification(self):
        """The launcher's own re-execution of every required check, on the FINAL state.

        This is what makes `succeeded` mean something: the agent's evidence describes some earlier
        state, and only a run on the final task-branch state can speak for what the developer would
        merge.
        """
        checks = []
        outputs = os.path.join(self.request.out, "checks")
        os.makedirs(outputs, exist_ok=True)
        for index, command in enumerate(self.request.verify, 1):
            status, out, err, timed_out = self.sbx.execute(
                self.sandbox, f"cd {WORKSPACE} && {command}", check=False, timeout=600)
            path = os.path.join(outputs, f"launcher-{index}.txt")
            with open(path, "w", encoding="utf-8") as handle:
                handle.write(out + ("\n" + err if err else ""))
            checks.append({
                "id": command,
                "command_or_method": command,
                "required": True,
                "executed_by": "launcher",
                "after_last_change": True,
                "result": "pass" if (status == 0 and not timed_out) else (
                    "unresolved" if timed_out else "fail"),
                "exit_status": None if timed_out else status,
                "output_ref": os.path.relpath(path, self.request.out),
            })
        return checks

    def collect_agent_evidence(self):
        """Copy the VM-originated evidence out. It is evidence, never authority."""
        collected = {}
        for name in ("context.json", "plan.md", "report.agent.json"):
            destination = os.path.join(self.request.out, name)
            try:
                self.sbx.copy_out(self.sandbox, f"{SCRATCH_DIR}/{name}", destination)
            except (SbxError, OSError):
                continue
            collected[name] = destination
        try:
            self.sbx.copy_out(self.sandbox, f"{STATE_DIR}/gate.log.jsonl",
                              os.path.join(self.request.out, "gate.log.jsonl"))
        except (SbxError, OSError):
            pass
        agent_report = None
        if "report.agent.json" in collected:
            try:
                with open(collected["report.agent.json"], encoding="utf-8") as handle:
                    agent_report = json.load(handle)
            except (OSError, ValueError):
                agent_report = None
        return agent_report

    def retrieve(self):
        """Export, copy out, validate in quarantine, and only then import. Failure is exit 4."""
        branch = f"dca/{self.request.run_id}"
        try:
            status, out, err, _ = self.sbx.execute(self.sandbox, (
                f"cd {WORKSPACE} && "
                "git add -A && "
                f"(git diff --cached --quiet || git commit --quiet -m 'dca {self.request.run_id}') && "
                f"git bundle create /tmp/dca-result.bundle refs/heads/{branch} >/dev/null 2>&1 && "
                f"git rev-parse refs/heads/{branch}"), check=False)
            if status != 0:
                raise InfraAbort(f"the task branch could not be exported: {(err or out)[:300]}")
            head = out.strip().splitlines()[-1].strip()
            quarantine = os.path.join(self.workdir, "quarantine")
            os.makedirs(quarantine, exist_ok=True)
            bundle = os.path.join(quarantine, "result.bundle")
            self.sbx.copy_out(self.sandbox, "/tmp/dca-result.bundle", bundle)
        except SbxError as exc:
            raise InfraAbort(f"retrieval failed: {exc}") from exc

        head_sha = source_module.import_result_bundle(
            self.request.repo, bundle, self.request.run_id, expected_commit=head,
            workdir=quarantine)
        files = source_module.change_set(self.request.repo, self.source_commit, head_sha)
        if not files:
            # An empty change set is kept out of the developer's repository: the branch would
            # promise a change that does not exist.
            source_module._git(self.request.repo, "branch", "-D", branch)
            return {"branch": None, "base_commit": self.source_commit, "head_commit": head_sha,
                    "files": []}
        return {"branch": branch, "base_commit": self.source_commit, "head_commit": head_sha,
                "files": files}

    def cleanup(self):
        """Always attempted, whatever happened. Disposal is mandatory."""
        if self.sandbox:
            self.sbx.remove(self.sandbox)
            self.sandbox = None
        if self.workdir:
            shutil.rmtree(self.workdir, ignore_errors=True)
            self.workdir = None

    # --- orchestration ----------------------------------------------------------------------------

    def run(self):
        """The whole lifecycle. Returns the process exit status the CLI should use."""
        request = self.request
        try:
            self.preconditions()
        except PreconditionError:
            raise

        blocked = self.policy_disposition()
        if blocked is not None:
            self.write_outputs(blocked)
            return report_module.exit_status(blocked)

        try:
            self.provision()
            analysis, host_stop, _exit_status, _stderr = self.execute_agent()
            agent_report = self.collect_agent_evidence()
            self.classification = _classification_of(agent_report) or self.classification
            launcher_checks = self.final_verification()
            change_set = self.retrieve()
        finally:
            self.cleanup()

        final = report_module.build(
            run_id=request.run_id, backend=request.backend, trust_level=request.trust,
            task_fingerprint=request.task_fingerprint,
            source={"ref": self.source_ref, "commit": self.source_commit,
                    "bundle_sha256": self.bundle_sha256,
                    "uncommitted_ignored": request.ignore_uncommitted,
                    "dirty_paths": list(self.dirty_paths)},
            sandbox_settings=self.sandbox_settings, analysis=analysis,
            agent_report=agent_report, change_set=change_set,
            limits_configured=self.host_limits(), versions=self.version_block(),
            launcher_checks=launcher_checks,
            limit_evidence_predates=_evidence_predates_limit(analysis, host_stop,
                                                             launcher_checks))
        self.write_outputs(final)
        return report_module.exit_status(final)

    def write_outputs(self, final):
        os.makedirs(self.request.out, exist_ok=True)
        report_module.validate(final)
        report_module.write(final, self.request.out)
        if (self.grants or {}).get("grants"):
            _write_json(os.path.join(self.request.out, "grants.json"), self.grants)


# --- small helpers ------------------------------------------------------------------------------


def _classification_of(agent_report):
    if not isinstance(agent_report, dict):
        return None
    value = (agent_report.get("classification") or {}).get("value")
    return value if value in ("direct", "planned") else None


def _evidence_predates_limit(analysis, host_stop, launcher_checks):
    """FR-023a, decided by the HOST because the report's checks carry no timestamps.

    A limit was reached only if the run stopped at one. When it did, success is permitted only if
    every required check already passed - and the launcher's own final re-execution happens AFTER
    the stop, so it is exactly the evidence that can establish it on the final state.
    """
    if analysis.limit_reached is None:
        return None
    required = [check for check in launcher_checks if check.get("required")]
    if not required:
        return False
    return all(check.get("result") == "pass" for check in required)


def _short_ref(ref):
    return ref[len("refs/heads/"):] if ref and ref.startswith("refs/heads/") else ref


def _shell_quote(value):
    return "'" + str(value).replace("'", "'\\''") + "'"


def _digest_of(value):
    import hashlib
    body = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return "sha256:" + hashlib.sha256(body.encode("utf-8")).hexdigest()


def _write_json(path, value):
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(value, handle, indent=2, sort_keys=True, ensure_ascii=False)
        handle.write("\n")


def _sideload_path(name, path):
    import importlib.util
    import sys

    module = sys.modules.get(name)
    if module is None:
        spec = importlib.util.spec_from_file_location(name, path)
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
    return module
