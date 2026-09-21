"""Host-side source delivery and result retrieval (tasks.md T038; R11, FR-030, FR-034).

Standard library only. Two directions cross the host/VM boundary and they are NOT symmetric:

  * OUTBOUND (`resolve_branch`, `worktree_state`, `create_source_bundle`) carries the developer's
    committed branch state into a fresh sandbox. The input is the developer's own repository, so
    the checks here are about delivering exactly what they asked for and nothing else: only a local
    branch, only committed content, never a working-tree file, never a temporary host ref.

  * INBOUND (`validate_result_bundle`, `import_result_bundle`) carries a bundle back OUT of a
    sandbox that ran a model. That direction is untrusted. Everything is validated in a quarantine
    directory outside `.git`, in a throwaway repository, and the developer's repository is not
    touched at all until every stage has passed.

WHY `git bundle verify` IS NOT ENOUGH ON THE INBOUND PATH. G5 observed a truncated bundle passing
`git bundle verify`, and this module's own tests reproduce it: verify reads the bundle header and
checks prerequisites, so a bundle whose packfile has been truncated or had bytes flipped still
verifies. Only unpacking the objects detects that. So validation fetches the bundle into an
isolated empty repository - which runs `index-pack` and therefore checks every object's hash - and
then walks the history. A validator built on `git bundle verify` alone would import corrupt history
into the developer's repository, which is exactly the failure this staging exists to prevent.

WHY REF VALIDATION IS SO NARROW. V1 delivers one local branch (`refs/heads/*`) and creates no
temporary host refs. A tag, a raw SHA or a revision expression would each need a temporary ref to
bundle from, and a remote-tracking ref names state the developer does not control. All of them are
refused at preflight (exit 3) rather than silently resolved to a commit, because "the branch you
are on" and "some commit that branch used to point at" are different promises.
"""

import hashlib
import os
import shutil
import subprocess
import tempfile

try:
    from .errors import InfraAbort, PreconditionError
except ImportError:  # loaded by path in tests and in the sandbox
    import importlib.util as _ilu
    import sys as _sys

    # The cache lookup is not an optimization. Loading errors.py twice would create two distinct
    # `InfraAbort` classes, and a caller that caught one would sail straight past the other.
    _errors = _sys.modules.get("dca_errors")
    if _errors is None:
        _spec = _ilu.spec_from_file_location(
            "dca_errors", os.path.join(os.path.dirname(os.path.abspath(__file__)), "errors.py"))
        _errors = _ilu.module_from_spec(_spec)
        _sys.modules["dca_errors"] = _errors
        _spec.loader.exec_module(_errors)
    InfraAbort = _errors.InfraAbort
    PreconditionError = _errors.PreconditionError

HEADS = "refs/heads/"
TAGS = "refs/tags/"
REMOTES = "refs/remotes/"

#: Environment git always runs under here. `GIT_CONFIG_NOSYSTEM` and an empty `HOME` keep a
#: developer's global config - hooks, `fetch.prune`, templates, an `init.defaultBranch` alias -
#: from changing what validation means. Quarantine validation must mean the same thing on every
#: machine, or "the bundle is sound" is a statement about one laptop.
_STRICT_ENV = {
    "GIT_CONFIG_NOSYSTEM": "1",
    "GIT_TERMINAL_PROMPT": "0",
    "GIT_ASKPASS": "",
    "GIT_ATTR_NOSYSTEM": "1",
}


def _run(args, cwd=None, env_home=None):
    """Run one git command. Returns (returncode, stdout, stderr) with text decoded permissively."""
    env = dict(os.environ)
    env.update(_STRICT_ENV)
    if env_home is not None:
        env["HOME"] = env_home
        env["XDG_CONFIG_HOME"] = os.path.join(env_home, ".config")
    proc = subprocess.run(
        args, cwd=cwd, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
    return (proc.returncode,
            proc.stdout.decode("utf-8", "replace"),
            proc.stderr.decode("utf-8", "replace"))


def _git(repo, *args):
    return _run(["git", "-C", str(repo), *args])


def _git_ok(repo, *args, what=None, error=InfraAbort):
    code, out, err = _git(repo, *args)
    if code != 0:
        raise error(f"{what or ' '.join(args)} failed: {err.strip() or out.strip()}")
    return out


# --- outbound: the developer's branch ------------------------------------------------------------


def is_git_repository(repo):
    code, out, _ = _git(repo, "rev-parse", "--is-inside-work-tree")
    return code == 0 and out.strip() == "true"


def require_repository(repo):
    if not is_git_repository(repo):
        raise PreconditionError(f"{repo} is not a git repository working tree")


def _ref_exists(repo, full_ref):
    code, _, _ = _git(repo, "show-ref", "--verify", "--quiet", full_ref)
    return code == 0


def resolve_branch(repo, ref=None):
    """Resolve the requested ref to exactly one LOCAL branch, or refuse (exit 3).

    Returns `(full_ref, commit)` where `full_ref` is always `refs/heads/<branch>`.

    Everything that is not a local branch is refused by name rather than resolved: a tag, a raw
    SHA, a revision expression, a remote-tracking ref, a name that is ambiguous between namespaces,
    and a detached `HEAD`. Refusing an ambiguous name matters even though git has a precedence
    order, because the developer's intent is genuinely unknown there and V1 delivers source, not a
    guess.
    """
    require_repository(repo)
    if ref is None or ref == "HEAD":
        code, out, _ = _git(repo, "symbolic-ref", "--quiet", "HEAD")
        if code != 0 or not out.strip().startswith(HEADS):
            raise PreconditionError(
                "HEAD is detached: dca delivers a local branch, so check out a branch or name one")
        full_ref = out.strip()
    elif ref.startswith(HEADS):
        if not _ref_exists(repo, ref):
            raise PreconditionError(f"no such local branch: {ref}")
        full_ref = ref
    elif ref.startswith(TAGS):
        raise PreconditionError(f"{ref} is a tag; dca delivers a local branch in V1")
    elif ref.startswith(REMOTES):
        raise PreconditionError(
            f"{ref} is a remote-tracking ref; dca delivers a local branch in V1")
    elif ref.startswith("refs/"):
        raise PreconditionError(f"{ref} is not a local branch ref")
    else:
        matches = [ns + ref for ns in (HEADS, TAGS, REMOTES) if _ref_exists(repo, ns + ref)]
        if len(matches) > 1:
            raise PreconditionError(
                f"{ref!r} is ambiguous ({', '.join(matches)}); name the full refs/heads/ ref")
        if not matches:
            raise PreconditionError(
                f"{ref!r} does not name a local branch (a tag, commit SHA or revision expression "
                "is refused in V1; no temporary host ref is ever created)")
        if not matches[0].startswith(HEADS):
            raise PreconditionError(f"{ref!r} resolves to {matches[0]}, which is not a local branch")
        full_ref = matches[0]

    code, out, err = _git(repo, "rev-parse", "--verify", "--quiet", full_ref + "^{commit}")
    if code != 0 or not out.strip():
        raise PreconditionError(f"{full_ref} does not resolve to a commit: {err.strip()}")
    return full_ref, out.strip()


def worktree_state(repo):
    """Every uncommitted or untracked non-ignored path, sorted. Ignored files never appear.

    `--untracked-files=normal` is deliberate: it reports untracked files but respects `.gitignore`,
    so build output and local scratch files - which are never delivered anyway - do not make a
    checkout look dirty.
    """
    require_repository(repo)
    out = _git_ok(repo, "status", "--porcelain=v1", "-z", "--untracked-files=normal",
                  what="git status", error=InfraAbort)
    records = out.split("\0")
    paths = []
    index = 0
    while index < len(records):
        record = records[index]
        index += 1
        if not record:
            continue
        status, _, path = record[0:2], record[2:3], record[3:]
        if status[0] in ("R", "C"):
            # A rename or copy record is followed by its source path in the next NUL field.
            if index < len(records):
                source = records[index]
                index += 1
                if source:
                    paths.append(source)
        if path:
            paths.append(path)
    return sorted(set(paths))


def check_clean(repo, ignore_uncommitted=False):
    """Refuse a dirty checkout (exit 3) unless the developer passed `--ignore-uncommitted`.

    Returns the dirty path NAMES, which is all the report ever records. Contents are never read:
    uncommitted work is not delivered, so quoting it into a report would leak it into an artifact
    the developer may share while the change itself stayed on their machine.
    """
    dirty = worktree_state(repo)
    if dirty and not ignore_uncommitted:
        raise PreconditionError(
            "the checkout has uncommitted or untracked changes and only committed state is "
            f"delivered: {', '.join(dirty[:10])}"
            + (f" (+{len(dirty) - 10} more)" if len(dirty) > 10 else "")
            + "; commit them or re-run with --ignore-uncommitted")
    return dirty


def file_sha256(path):
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def create_source_bundle(repo, full_ref, commit, destination):
    """Bundle the branch ref itself, then prove the bundle says what it should. Failure = exit 4.

    The bundle is created FROM THE BRANCH REF (R11), not from a commit and not from a temporary
    ref, so nothing is added to the developer's repository and the bundle's single advertised head
    is the branch they named.
    """
    destination = str(destination)
    parent = os.path.dirname(os.path.abspath(destination))
    if parent:
        try:
            os.makedirs(parent, exist_ok=True)
        except OSError as exc:
            raise InfraAbort(f"the source bundle directory {parent} is unusable: {exc}") from exc
    code, _, err = _git(repo, "bundle", "create", destination, full_ref)
    if code != 0 or not os.path.exists(destination):
        raise InfraAbort(f"creating the source bundle failed: {err.strip()}")

    heads = _bundle_heads(repo, destination)
    if len(heads) != 1:
        raise InfraAbort(
            f"the source bundle advertises {len(heads)} heads, expected exactly one")
    head_sha, head_ref = heads[0]
    if head_ref != full_ref:
        raise InfraAbort(
            f"the source bundle advertises {head_ref}, expected {full_ref}")
    if head_sha != commit:
        raise InfraAbort(
            f"the source bundle head is {head_sha}, expected the branch commit {commit}")
    code, _, err = _git(repo, "bundle", "verify", destination)
    if code != 0:
        raise InfraAbort(f"git bundle verify rejected the source bundle: {err.strip()}")
    return file_sha256(destination)


# --- inbound: what came back out of the sandbox --------------------------------------------------


def _bundle_heads(repo, bundle_path):
    """[(sha, ref)] as the bundle ADVERTISES them. Advertisement is a claim, not yet evidence."""
    code, out, err = _git(repo, "bundle", "list-heads", str(bundle_path))
    if code != 0:
        raise InfraAbort(f"the bundle has no readable head list: {err.strip() or out.strip()}")
    heads = []
    for line in out.splitlines():
        line = line.strip()
        if not line:
            continue
        parts = line.split(None, 1)
        if len(parts) != 2:
            raise InfraAbort(f"unreadable bundle head line: {line!r}")
        heads.append((parts[0], parts[1]))
    return heads


def validate_result_bundle(bundle_path, expected_ref, expected_commit=None, workdir=None):
    """Validate a returned bundle IN QUARANTINE, before any host repository is touched.

    Stages, in order, each one fail-closed:
      1. the file exists and is non-empty;
      2. `git bundle verify` passes - necessary, and on its own worthless (see the module docstring);
      3. the advertised identity is exactly one head, on exactly `expected_ref`, at
         `expected_commit` when the caller knows it;
      4. OBJECT-LEVEL: every object unpacks into an isolated empty repository, which is what
         actually catches a truncated or byte-flipped packfile;
      5. the isolated repository passes `git fsck --full --strict` and the whole history from the
         head is walkable, so a bundle cannot advertise a head it does not really contain.

    Returns the head SHA. Any failure raises `InfraAbort` (exit 4): the developer gets no report and
    no `dca/<run-id>` ref, because a change set that cannot be trusted must not be reviewable as if
    it were.
    """
    bundle_path = os.path.abspath(str(bundle_path))
    if not os.path.isfile(bundle_path) or os.path.getsize(bundle_path) == 0:
        raise InfraAbort(f"the returned bundle is missing or empty: {bundle_path}")

    owned = workdir is None
    workdir = tempfile.mkdtemp(prefix="dca-quarantine-") if owned else str(workdir)
    isolated = os.path.join(workdir, "isolated.git")
    fake_home = os.path.join(workdir, "home")
    try:
        os.makedirs(fake_home, exist_ok=True)
        if os.path.exists(isolated):
            shutil.rmtree(isolated)
        code, _, err = _run(["git", "init", "--bare", "--quiet", isolated], env_home=fake_home)
        if code != 0:
            raise InfraAbort(f"the quarantine repository could not be created: {err.strip()}")

        # Stage 2. `git bundle verify` needs to run inside a repository; the isolated one is the
        # only repository this function is allowed to touch.
        code, out, err = _run(["git", "-C", isolated, "bundle", "verify", bundle_path],
                              env_home=fake_home)
        if code != 0:
            raise InfraAbort(
                f"git bundle verify rejected the returned bundle: {err.strip() or out.strip()}")

        # Stage 3.
        heads = _bundle_heads(isolated, bundle_path)
        if len(heads) != 1:
            raise InfraAbort(
                f"the returned bundle advertises {len(heads)} heads, expected exactly one")
        head_sha, head_ref = heads[0]
        if head_ref != expected_ref:
            raise InfraAbort(
                f"the returned bundle advertises {head_ref}, expected exactly {expected_ref}")
        if expected_commit is not None and head_sha != expected_commit:
            raise InfraAbort(
                f"the returned bundle head is {head_sha}, expected {expected_commit}")

        # Stage 4: this is the stage `git bundle verify` cannot stand in for.
        code, out, err = _run(
            ["git", "-C", isolated, "fetch", "--no-tags", "--quiet", bundle_path,
             f"{expected_ref}:{expected_ref}"], env_home=fake_home)
        if code != 0:
            raise InfraAbort(
                "object-level validation rejected the returned bundle "
                f"(it passed git bundle verify): {err.strip() or out.strip()}")

        # Stage 5.
        code, out, err = _run(["git", "-C", isolated, "fsck", "--full", "--strict",
                               "--no-dangling"], env_home=fake_home)
        if code != 0:
            raise InfraAbort(f"the returned history failed fsck: {err.strip() or out.strip()}")
        code, out, err = _run(["git", "-C", isolated, "rev-list", "--objects", expected_ref],
                              env_home=fake_home)
        if code != 0:
            raise InfraAbort(
                f"the returned history is not walkable from {expected_ref}: {err.strip()}")
        code, out, err = _run(["git", "-C", isolated, "rev-parse", "--verify", expected_ref],
                              env_home=fake_home)
        if code != 0 or out.strip() != head_sha:
            raise InfraAbort(
                f"the returned bundle advertised {head_sha} but {expected_ref} resolved to "
                f"{out.strip() or 'nothing'}")
        return head_sha
    finally:
        if owned:
            shutil.rmtree(workdir, ignore_errors=True)


def import_result_bundle(repo, bundle_path, run_id, expected_commit=None, workdir=None):
    """Validate in quarantine, then create `dca/<run_id>` in the host repository. All-or-nothing.

    The host ref is created ONLY after every quarantine stage has passed, so a rejected bundle
    leaves the developer's repository byte-for-byte as it was. There is no checkout and no merge;
    the working tree is never touched.
    """
    require_repository(repo)
    expected_ref = f"refs/heads/dca/{run_id}"
    head_sha = validate_result_bundle(bundle_path, expected_ref, expected_commit, workdir)
    code, out, err = _git(repo, "fetch", "--no-tags", "--quiet", os.path.abspath(str(bundle_path)),
                          f"{expected_ref}:{expected_ref}")
    if code != 0:
        raise InfraAbort(f"importing the validated bundle failed: {err.strip() or out.strip()}")
    code, out, _ = _git(repo, "rev-parse", "--verify", expected_ref)
    if code != 0 or out.strip() != head_sha:
        raise InfraAbort(f"{expected_ref} was not created at {head_sha} after import")
    return head_sha


def change_set(repo, base_commit, head_commit):
    """`{branch-relative} files[] {path, status}` computed by the LAUNCHER from the imported history.

    The agent's own list is never used: the change set is what the developer will review, so it is
    derived from the objects that actually arrived.
    """
    out = _git_ok(repo, "diff", "--name-status", "-z", "--no-renames",
                  f"{base_commit}..{head_commit}", what="git diff", error=InfraAbort)
    fields = [f for f in out.split("\0") if f != ""]
    letters = {"A": "added", "M": "modified", "D": "deleted", "R": "renamed", "C": "added",
               "T": "modified"}
    files = []
    for index in range(0, len(fields) - 1, 2):
        status = fields[index][0]
        files.append({"path": fields[index + 1], "status": letters.get(status, "modified")})
    files.sort(key=lambda item: item["path"])
    return files
