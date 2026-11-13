"""The `sbx` command surface the launcher uses (tasks.md T064, T067).

Standard library only. Every `sbx` call the launcher makes goes through here, for three reasons
that are each a requirement rather than a convenience:

  * **`SSH_AUTH_SOCK` is removed from every invocation.** FR-029b and precondition 5 are about the
    VM never reaching a host SSH agent, and one forgotten call site would be enough to break that.
    Removing it in one place makes the property structural instead of a habit.
  * **Global settings are read, never written.** `sbx policy init`, `sbx reset` and `sbx rm --all`
    have no wrapper here at all, so the launcher cannot call them by accident. The only global
    state it touches is read-only: the settings it checks and the network-policy fingerprint it
    compares. Changing a global setting is a developer-authorized gate action, never a run action.
  * **Every invocation is recorded.** `calls` holds the exact argv of everything that ran, which is
    what lets the integration tests assert on the command the launcher actually built - the
    `--safety strict` pin, the sandbox base, `--skills off` - rather than on what a helper claims
    it would build.
"""

import json
import os
import subprocess


def sandbox_inventory(document):
    """Normalize observed sbx inventories: v0.43's wrapper and v0.53's array.

    Missing fields, error envelopes and malformed entries remain unreadable, never empty.
    """
    listing = (document["sandboxes"] if isinstance(document, dict)
               and set(document) == {"sandboxes"} else document)
    if not isinstance(listing, list) or any(
            not isinstance(entry, dict) or not isinstance(entry.get("name"), str)
            or not entry["name"] for entry in listing):
        raise ValueError("expected a sandbox inventory with nonempty names")
    return listing


VERSIONS = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))), "runtime", "versions.yaml")


def kit_build_pins(versions_path=VERSIONS):
    """runtime/versions.yaml's `kit_build`: the buildx builder, its BuildKit image, the frontend."""
    with open(versions_path, encoding="utf-8") as handle:
        return json.load(handle)["kit_build"]


def kit_builder(versions_path=VERSIONS):
    return kit_build_pins(versions_path)["builder"]


def composition_problem(record, base, kit):
    """Why `sbx inspect --json` does NOT prove the sandbox is exactly `base` + `kit`, or None.

    The sandbox's own record must list EXACTLY the pinned workload reference and the DCA mixin,
    in that order: an extra kit (a shortcut, an injected mixin) or a workload resolved from a
    tag is a different composition, and an unprovable composition is refused like a wrong one.
    """
    if not isinstance(record, dict):
        return "sbx inspect returned no sandbox record"
    kits = record.get("kits")
    expected = [base, os.path.abspath(str(kit))]
    if kits != expected:
        return f"the sandbox is composed of {kits!r}, not exactly {expected!r}"
    return None


class SbxError(RuntimeError):
    """An `sbx` invocation failed. The caller decides whether that is exit 3 or exit 4."""

    def __init__(self, argv, status, stderr):
        super().__init__(f"sbx {' '.join(argv[1:])} exited {status}: {stderr.strip()[:400]}")
        self.argv = argv
        self.status = status
        self.stderr = stderr


class Sbx:
    """A thin, recorded wrapper. `binary` lets the tests point at `tests/fakes/sbx`."""

    def __init__(self, binary="sbx", env=None):
        self.binary = binary
        self.calls = []
        self._env = dict(env or {})

    # --- process ----------------------------------------------------------------------------

    def environment(self):
        """The environment every sbx invocation runs under, including a streamed one.

        Public because the launcher streams `sbx exec` itself to enforce limits mid-run; it must
        use THIS environment, or `SSH_AUTH_SOCK` removal would apply only to the calls that happen
        to go through `run()`.
        """
        return self._environment()

    def _environment(self):
        environment = dict(os.environ)
        # sbx builds the v3 mixin with the host's buildx, and the default `docker` driver cannot
        # export it: the pinned docker-container builder is selected per process, never `--use`d.
        environment["BUILDX_BUILDER"] = kit_builder()
        environment.update(self._env)
        # FR-029b / precondition 5: the VM never inherits a host SSH agent.
        environment.pop("SSH_AUTH_SOCK", None)
        return environment

    def run(self, *args, check=True, stdin=None):
        argv = [self.binary, *[str(a) for a in args]]
        proc = subprocess.run(
            argv, env=self._environment(),
            stdin=subprocess.DEVNULL if stdin is None else stdin,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
        out = proc.stdout.decode("utf-8", "replace")
        err = proc.stderr.decode("utf-8", "replace")
        self.calls.append({"argv": argv, "status": proc.returncode})
        if check and proc.returncode != 0:
            raise SbxError(argv, proc.returncode, err)
        return proc.returncode, out, err

    def _json(self, *args):
        _, out, _ = self.run(*args)
        try:
            return json.loads(out)
        except ValueError as exc:
            raise SbxError([self.binary, *map(str, args)], 0, f"not JSON: {exc}") from exc

    # --- read-only global state ---------------------------------------------------------------

    def version(self):
        _, out, _ = self.run("version")
        return out.strip()

    def list_sandboxes(self):
        document = self._json("ls", "--json")
        try:
            return sandbox_inventory(document)
        except ValueError as exc:
            raise SbxError([self.binary, "ls", "--json"], 0,
                           str(exc)) from exc

    def policy(self):
        """The whole policy document, including global rules and governance. Read-only."""
        return self._json("policy", "ls", "--json")

    def settings(self):
        """`{key: value}` for every global sbx setting.

        `sbx settings ls --json` answers with a LIST of setting objects, each carrying `key` and
        `value` among its metadata. The launcher asks questions by key, so the list is turned into
        a mapping here - once, next to the command that produces it - rather than in the caller.
        A shape this does not recognise raises instead of returning an empty mapping: precondition
        5 must refuse an unreadable SSH setting, never read it as "no agent".
        """
        document = self._json("settings", "ls", "--json")
        if isinstance(document, dict):
            return document
        if not isinstance(document, list):
            raise SbxError([self.binary, "settings", "ls", "--json"], 0,
                           f"settings is a {type(document).__name__}, not a list of settings")
        mapping = {}
        for entry in document:
            if not isinstance(entry, dict) or "key" not in entry:
                raise SbxError([self.binary, "settings", "ls", "--json"], 0,
                               "a settings entry has no key")
            mapping[entry["key"]] = entry.get("value")
        return mapping

    # --- per-sandbox lifecycle -------------------------------------------------------------------

    def create(self, name, kit, base):
        """A mountless sandbox with shared skills OFF, the V1 kit and the EXACT pinned base.

        `base` is the immutable, digest-qualified v3 workload kit - `<repository>@sha256:<hex>` -
        and it is REQUIRED. sbx >= 0.53 resolves a bare agent name (`claude`) to that kit's
        mutable `:latest`, so the pinned reference is passed as the AGENT itself: the digest is a
        creation-time input, never something resolved from a tag and audited afterwards. `kit` is
        the absolute path of the DCA v3 mixin directory; sbx builds it with the host's buildx, so
        the builder named in the environment (BUILDX_BUILDER) decides how it is built.
        `--static-mcp none` gives the sandbox an empty static MCP set: V1 has no MCP.
        """
        if not base or "@sha256:" not in base:
            raise ValueError("a pinned, digest-qualified base reference is required to create a "
                             "sandbox; the agent default would follow a mutable tag")
        _, out, _ = self.run("create", base, "--name", name, "--skills", "off",
                             "--static-mcp", "none", "--kit", kit)
        return out

    def inspect(self, name):
        """`sbx inspect <name> --json`: the sandbox's own record of what it was composed from."""
        return self._json("inspect", name, "--json")

    def allow_network(self, name, hosts):
        if not hosts:
            return
        self.run("policy", "allow", "network", "--sandbox", name, ",".join(hosts))

    def deny_network(self, name, hosts):
        if not hosts:
            return
        self.run("policy", "deny", "network", "--sandbox", name, ",".join(hosts))

    def copy_in(self, source, name, destination):
        self.run("cp", str(source), f"{name}:{destination}")

    def copy_out(self, name, source, destination):
        self.run("cp", f"{name}:{source}", str(destination))

    def execute(self, name, script, check=True, timeout=None):
        """Run `sh -c <script>` in the sandbox with stdin closed.

        Stdin is always closed: `docker agent run --exec --json` must be non-interactive, and a
        run that could block on a prompt would hang instead of failing.
        """
        argv = [self.binary, "exec", name, "sh", "-c", script]
        proc = subprocess.Popen(argv, env=self._environment(), stdin=subprocess.DEVNULL,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        try:
            stdout, stderr = proc.communicate(timeout=timeout)
            timed_out = False
        except subprocess.TimeoutExpired:
            proc.kill()
            stdout, stderr = proc.communicate()
            timed_out = True
        out = stdout.decode("utf-8", "replace")
        err = stderr.decode("utf-8", "replace")
        self.calls.append({"argv": argv, "status": proc.returncode, "timed_out": timed_out})
        if check and not timed_out and proc.returncode != 0:
            raise SbxError(argv, proc.returncode, err)
        return proc.returncode, out, err, timed_out

    def remove(self, name):
        """Best-effort removal. Disposal is mandatory, so a failure here is reported, not raised."""
        status, _, err = self.run("rm", "--force", name, check=False)
        return status, err
