"""Workspace fingerprint (tasks.md T042; R16, FR-022).

Standard library only. One question: **did anything in this workspace change while the read-only
reviewer was looking at it?** FR-022 makes a changed candidate a safety-invariant violation, so the
fingerprint has to notice every change the reviewer could have been misled by, and has to give the
same answer twice for the same tree or it would raise that violation on a run where nothing
happened.

WHAT IT COVERS, AND WHY EACH PART IS NEEDED:

  * **HEAD** - a commit, amend, reset or branch switch moves the candidate even when no file on
    disk differs;
  * **the index** - staged content that is not yet committed and not yet on disk is still state the
    next commit would capture;
  * **the worktree**, including **untracked non-ignored files** - the actual bytes a reviewer reads,
    plus files that exist only in the working tree;
  * **file modes** - a chmod +x changes what the change set does without changing a single byte of
    content, and a symlink is recorded by its target rather than by what it points at.

Ignored files are excluded, exactly as they are excluded from delivery and from the dirty-checkout
check: build output churns constantly and is never part of the candidate, so including it would
make the fingerprint differ for reasons that have nothing to do with the change.

The digest is over a canonical, sorted text form, so it does not depend on directory-listing order,
on the platform, or on how many times it is computed.
"""

import hashlib
import os
import subprocess

BLOB = "100644"
EXECUTABLE = "100755"
SYMLINK = "120000"
MISSING = "missing"
PREFIX = "sha256:"

_ENV = {"GIT_CONFIG_NOSYSTEM": "1", "GIT_TERMINAL_PROMPT": "0"}


def _git(repo, *args):
    env = dict(os.environ)
    env.update(_ENV)
    proc = subprocess.run(["git", "-C", str(repo), *args], env=env,
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
    return proc.returncode, proc.stdout


def _git_text(repo, *args):
    code, out = _git(repo, *args)
    if code != 0:
        return None
    return out.decode("utf-8", "surrogateescape")


def _zsplit(text):
    return [field for field in (text or "").split("\0") if field != ""]


def _content_digest(path):
    """SHA-256 of what is at `path`, with the kind of entry folded in.

    A symlink is digested by its TARGET, never by following it: following would make the
    fingerprint depend on a file the workspace does not contain, and would loop on a cycle.
    """
    try:
        stat = os.lstat(path)
    except OSError:
        return MISSING, MISSING
    if os.path.islink(path):
        target = os.readlink(path).encode("utf-8", "surrogateescape")
        return SYMLINK, hashlib.sha256(b"link\0" + target).hexdigest()
    if not os.path.isfile(path):
        # A path that is a directory here but a file in the index is a real difference, and
        # recording it as such is what makes it visible.
        return MISSING, MISSING
    mode = EXECUTABLE if stat.st_mode & 0o111 else BLOB
    digest = hashlib.sha256()
    digest.update(b"blob\0")
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return mode, digest.hexdigest()


def fingerprint_lines(repo):
    """The canonical, sorted lines the digest is taken over. Exposed so a mismatch is explainable."""
    repo = str(repo)
    # The fingerprint must describe THIS workspace. Without this check git would walk up to an
    # enclosing repository and happily fingerprint a parent directory, which would compare two
    # different trees and call them identical.
    toplevel = _git_text(repo, "rev-parse", "--show-toplevel")
    if toplevel is None:
        raise RuntimeError(f"{repo} is not a readable git repository")
    if os.path.realpath(toplevel.strip()) != os.path.realpath(repo):
        raise RuntimeError(
            f"{repo} is not the root of a git repository (its repository root is "
            f"{toplevel.strip()})")
    lines = []

    head = _git_text(repo, "rev-parse", "HEAD")
    lines.append("head\t" + (head.strip() if head else "unborn"))

    branch = _git_text(repo, "symbolic-ref", "--quiet", "HEAD")
    lines.append("branch\t" + (branch.strip() if branch else "detached"))

    code, raw = _git(repo, "ls-files", "--stage", "-z")
    if code != 0:
        raise RuntimeError(f"{repo} is not a readable git repository")
    for record in _zsplit(raw.decode("utf-8", "surrogateescape")):
        meta, _, path = record.partition("\t")
        mode, object_id, stage = (meta.split() + ["", "", ""])[:3]
        lines.append(f"index\t{mode}\t{object_id}\t{stage}\t{path}")

    code, raw = _git(repo, "ls-files", "-z", "--cached", "--others", "--exclude-standard")
    if code != 0:
        raise RuntimeError(f"{repo} is not a readable git repository")
    for path in sorted(set(_zsplit(raw.decode("utf-8", "surrogateescape")))):
        mode, digest = _content_digest(os.path.join(repo, path))
        lines.append(f"work\t{mode}\t{digest}\t{path}")

    return sorted(lines)


def workspace_fingerprint(repo):
    """`sha256:<hex>` over HEAD, the index and the worktree including untracked non-ignored files."""
    body = "\n".join(fingerprint_lines(repo)) + "\n"
    return PREFIX + hashlib.sha256(body.encode("utf-8", "surrogateescape")).hexdigest()
