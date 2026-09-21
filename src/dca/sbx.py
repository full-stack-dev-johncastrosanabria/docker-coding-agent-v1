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
        return (self._json("ls", "--json") or {}).get("sandboxes") or []

    def policy(self):
        """The whole policy document, including global rules and governance. Read-only."""
        return self._json("policy", "ls", "--json")

    def settings(self):
        return self._json("settings", "ls", "--json")

    def templates(self):
        return self._json("template", "ls", "--json")

    # --- per-sandbox lifecycle -------------------------------------------------------------------

    def create(self, template, name, kit):
        """A mountless sandbox with shared skills OFF and the V1 kit. No host path is mounted."""
        _, out, _ = self.run("create", template, "--name", name, "--skills", "off", "--kit", kit)
        return out

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
