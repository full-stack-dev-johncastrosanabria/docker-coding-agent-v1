"""Per-checkout DCA settings: repository detection and `<git-dir>/dca/config.toml`.

Standard library only (`tomllib`, Python 3.11+).

WHERE THE FILE LIVES IS THE SECURITY PROPERTY. The config can select `trust = "trusted"` and the
verification commands, so it must be something only the developer wrote. It lives inside the
checkout's git directory (`.git/dca/config.toml`, or the worktree's own git directory):

  * git never checks anything out there, so a cloned repository cannot ship one;
  * `git status` never reports it, so it cannot make the checkout look dirty;
  * `git bundle` carries refs and objects only, so it never reaches the sandbox.

A copied or unpacked tree could still carry a `.git` directory. The file therefore records the
repository root it was created for, and a mismatch is refused: settings written for one checkout
are never applied to another.

UNKNOWN AND MALFORMED CONTENT FAILS CLOSED. Only `backend`, `trust` and the verification commands
can be set. Every other key - `safety`, `network`, `allow_drift`, credentials, anything - is
refused rather than ignored, because a setting that is silently ignored teaches the person that
it worked.
"""

import json
import os
import stat
import subprocess

try:
    import tomllib
except ImportError:  # pragma: no cover - Python < 3.11 is outside the supported range
    tomllib = None

try:
    from .errors import UsageError
except ImportError:  # loaded by path in tests
    import importlib.util
    import sys

    _errors = sys.modules.get("dca_errors")
    if _errors is None:
        _spec = importlib.util.spec_from_file_location(
            "dca_errors", os.path.join(os.path.dirname(os.path.abspath(__file__)), "errors.py"))
        _errors = importlib.util.module_from_spec(_spec)
        sys.modules["dca_errors"] = _errors
        _spec.loader.exec_module(_errors)
    UsageError = _errors.UsageError

VERSION = 1
BACKENDS = ("claude", "codex")
TRUST_LEVELS = ("trusted", "untrusted")
DEFAULT_BACKEND = "claude"
#: FR-029a. The config can choose `trusted`; nothing else ever does it for the developer.
DEFAULT_TRUST = "untrusted"

TOP_LEVEL_KEYS = ("version", "repository", "backend", "trust", "verification")
VERIFICATION_KEYS = ("commands",)


class ProjectConfig:
    def __init__(self, path, repository, backend=None, trust=None, verify=None):
        self.path = path
        self.repository = repository
        self.backend = backend
        self.trust = trust
        self.verify = list(verify) if verify is not None else None


# --- repository detection --------------------------------------------------------------------------


def _git(directory, *args):
    try:
        proc = subprocess.run(["git", "-C", directory, *args], stdout=subprocess.PIPE,
                              stderr=subprocess.PIPE, check=False)
    except OSError as exc:
        raise UsageError(f"git could not be run: {exc}") from exc
    return proc.returncode, proc.stdout.decode("utf-8", "replace").strip()


def detect_repository(start=None):
    """The root of the git repository that contains `start` (default: the current directory).

    A directory that sits inside a repository without being part of its committed tree - an
    unpacked download inside a home directory that happens to be a repository, say - is refused
    rather than resolved to that unrelated parent.
    """
    start = os.path.abspath(start or os.getcwd())
    code, root = _git(start, "rev-parse", "--show-toplevel")
    if code != 0 or not root:
        raise UsageError(f"{start} is not inside a Git repository. cd into your project's "
                         "checkout, or pass --repo <path>.")
    code, prefix = _git(start, "rev-parse", "--show-prefix")
    if code == 0 and prefix:
        code, head = _git(root, "rev-parse", "--verify", "--quiet", "HEAD")
        if code == 0 and head:
            _, listed = _git(root, "ls-tree", "-d", "--name-only", "HEAD", "--",
                             prefix.rstrip("/"))
            if not listed:
                raise UsageError(
                    f"{start} is inside the Git repository {root} but is not part of its "
                    "committed tree, so DCA will not assume you meant that repository. cd into "
                    "your project's checkout, or pass --repo <path>.")
    return os.path.realpath(root)


def config_path(repo):
    """`<git-dir>/dca/config.toml` for the checkout rooted at `repo`, or None if `repo` is not one.

    Only a checkout ROOT has a config: a path inside a repository is not the repository, and the
    launcher's own precondition refuses it with the diagnostic the CLI contract defines.
    """
    if not os.path.isdir(repo):
        return None
    code, root = _git(repo, "rev-parse", "--show-toplevel")
    if code != 0 or os.path.realpath(root) != os.path.realpath(repo):
        return None
    code, git_dir = _git(repo, "rev-parse", "--absolute-git-dir")
    if code != 0 or not git_dir:
        return None
    return os.path.join(git_dir, "dca", "config.toml")


# --- reading ----------------------------------------------------------------------------------------


def load(repo):
    """The checkout's validated config, or None when there is none. Anything invalid is refused."""
    path = config_path(repo)
    if path is None or not os.path.lexists(path):
        return None
    where = f"the local DCA config {path}"
    if tomllib is None:
        raise UsageError(f"{where} needs Python 3.11 or newer to read (tomllib)")
    if not os.path.isfile(path) or os.path.islink(path):
        raise UsageError(f"{where} is not a regular file")
    mode = os.stat(path).st_mode
    if mode & (stat.S_IWGRP | stat.S_IWOTH):
        raise UsageError(f"{where} is writable by other users, so it cannot carry trust "
                         f"settings; run `chmod 600 {path}`")
    try:
        with open(path, "rb") as handle:
            document = tomllib.load(handle)
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
        raise UsageError(f"{where} could not be read: {exc}. Fix it, or recreate it with "
                         "`dca init --overwrite`.") from exc

    def refuse(problem):
        raise UsageError(f"{where}: {problem}. Fix it, or recreate it with "
                         "`dca init --overwrite`.")

    unknown = sorted(set(document) - set(TOP_LEVEL_KEYS))
    if unknown:
        refuse(f"unknown setting(s) {', '.join(unknown)}; DCA reads only backend, trust and "
               "[verification] commands, and refuses anything else rather than ignoring it")
    if document.get("version") != VERSION:
        refuse(f"version must be {VERSION}")
    repository = document.get("repository")
    if not isinstance(repository, str) or not repository:
        refuse("repository must name the checkout the file was created for")
    if os.path.realpath(repository) != os.path.realpath(repo):
        refuse(f"it was created for {repository}, not this checkout ({repo})")
    backend = document.get("backend")
    if backend is not None and backend not in BACKENDS:
        refuse(f"backend must be one of {', '.join(BACKENDS)}")
    trust = document.get("trust")
    if trust is not None and trust not in TRUST_LEVELS:
        refuse(f"trust must be one of {', '.join(TRUST_LEVELS)}")
    verify = None
    verification = document.get("verification")
    if verification is not None:
        if not isinstance(verification, dict):
            refuse("[verification] must be a table")
        extra = sorted(set(verification) - set(VERIFICATION_KEYS))
        if extra:
            refuse(f"unknown [verification] setting(s) {', '.join(extra)}")
        commands = verification.get("commands", [])
        if not isinstance(commands, list) or not all(
                isinstance(item, str) and item.strip() for item in commands):
            refuse("[verification] commands must be a list of non-empty strings")
        verify = list(commands)
    return ProjectConfig(path, repository, backend=backend, trust=trust, verify=verify)


# --- writing ----------------------------------------------------------------------------------------


def _string(value):
    # A JSON string is a valid TOML basic string: every escape json.dumps emits is also TOML's.
    return json.dumps(value)


def render(repository, backend=DEFAULT_BACKEND, trust=DEFAULT_TRUST, verify=()):
    commands = ", ".join(_string(command) for command in verify)
    return "\n".join([
        "# DCA settings for this checkout. This file lives in the git directory: it is never",
        "# committed, never cloned and never sent to the sandbox. Command-line options override it.",
        f"version = {VERSION}",
        f"repository = {_string(repository)}",
        f"backend = {_string(backend)}",
        "# trust = \"trusted\" runs this repository's content as trusted. Set it only for code you",
        "# trust; V1 blocks untrusted runs.",
        f"trust = {_string(trust)}",
        "",
        "[verification]",
        "# Commands the launcher re-runs in the sandbox on the final state. --verify replaces them.",
        f"commands = [{commands}]",
        "",
    ])


def write(repo, content, overwrite=False):
    """Create the config. Returns (path, changed). An existing, different file needs `overwrite`."""
    path = config_path(repo)
    if path is None:
        raise UsageError(f"{repo} is not the root of a Git checkout")
    if os.path.lexists(path):
        try:
            with open(path, encoding="utf-8") as handle:
                if handle.read() == content:
                    return path, False
        except (OSError, UnicodeDecodeError):
            pass
        if not overwrite:
            raise UsageError(f"{path} already exists with different settings; nothing was "
                             "changed. Re-run with --overwrite to replace it.")
    os.makedirs(os.path.dirname(path), mode=0o700, exist_ok=True)
    temporary = path + ".tmp"
    descriptor = os.open(temporary,
                         os.O_WRONLY | os.O_CREAT | os.O_TRUNC | getattr(os, "O_NOFOLLOW", 0),
                         0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        handle.write(content)
    os.chmod(temporary, 0o600)
    os.replace(temporary, path)
    return path, True
