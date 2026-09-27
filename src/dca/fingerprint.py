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
import json
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

# --- the managed review record (FR-022, host-authoritative) ---------------------------------------
#
# `fingerprint_hook.py` appends one JSON object per delegation boundary to
# /run/dca/state/fingerprints.jsonl. The host copies that file out and decides FR-022 from it. The
# AGENT is never asked to read it: making the root model re-read internal runtime state to prove a
# host invariant is both unnecessary and backend-dependent - one backend's root session could read
# it and another's could not, so the same correct run passed on one and was blocked on the other.
#
# Event names differ per harness - SubagentStart/SubagentStop on one, on_agent_switch/subagent_stop
# on the other - so a side is derived from GENERIC tokens rather than from any provider's spelling.
# Nothing here is provider-specific. The root's own turn_end/stop hooks land in the same file and
# are NOT a third spelling of this pair: they record the run, on a different agent, for a different
# purpose, and the reviewer attribution below is what keeps them out of the comparison.

REVIEWER = "review"
START_TOKENS = ("start", "switch", "begin")
STOP_TOKENS = ("stop", "end")


def _recorded_digest(record):
    """The usable `sha256:<hex>` this record carries, or None.

    A record whose hook failed carries `error` and a null fingerprint. That is evidence that is
    ABSENT, never evidence of sameness, so it yields None and the identity stays unproven.
    """
    if record.get("error"):
        return None
    value = record.get("fingerprint")
    if not isinstance(value, str) or not value.startswith(PREFIX):
        return None
    return value if len(value) > len(PREFIX) else None


def _side(event):
    """`start`, `stop`, or None - by generic token, so no harness's event spelling is privileged."""
    lowered = str(event or "").lower()
    if any(token in lowered for token in STOP_TOKENS):
        return "stop"
    if any(token in lowered for token in START_TOKENS):
        return "start"
    return None


def review_identity(records):
    """The host's FR-022 verdict on `records`, in the order the hook appended them.

    THE SHAPE OF A DELEGATION IS NOT THE SAME ON BOTH BACKENDS, and a live record is what settles
    it. One harness fires the reviewer's own start and stop, so the reviewer owns both sides. The
    other fires only a `subagent_stop` for the reviewer and brackets it with the ROOT's switch
    records, because a switch payload names the executing agent rather than the agent entered - so
    on that backend the reviewer has a stop and no start of its own. An implementation that demanded
    a reviewer-attributed start therefore proved nothing on that backend and would have blocked
    every planned run there; `gates/production/behavior.py` had already worked this out from real
    evidence, and this follows the same reading. Its gate check additionally requires the trailing
    switch to match, which is a stronger, independent cross-check on the same record.

    So the window is: the nearest start-side fingerprint BEFORE the reviewer's stop, whoever it is
    attributed to, and the reviewer's stop itself. That brackets exactly the reviewer's execution.

    Proven only when both sides exist and are equal. Everything else is unproven, and unproven is
    never treated as identical:

      * a mismatch is a reviewer-immutability violation - the candidate moved while it was read;
      * a missing, unreadable or unattributable side proves nothing, so a planned task cannot be
        reported a success on it. Absent evidence is not permission.
    """
    verdict = {"proven": False, "mismatch": False, "fingerprint_before": None,
               "fingerprint_after": None, "agent": None, "reason": ""}
    ordered = [r for r in records if isinstance(r, dict)]
    if not ordered:
        verdict["reason"] = "the managed fingerprint record is empty or was not retrieved"
        return verdict
    sessions = []
    for index, record in enumerate(ordered):
        if REVIEWER not in str(record.get("agent") or "").lower():
            continue
        if _side(record.get("event")) != "stop":
            continue
        after = _recorded_digest(record)
        before = None
        for earlier in reversed(ordered[:index]):
            if _side(earlier.get("event")) == "start":
                before = _recorded_digest(earlier)
                break
        sessions.append((str(record.get("agent")), before, after))
    if not sessions:
        verdict["reason"] = ("no managed fingerprint records the reviewer finishing, so no window "
                             "can be shown to bracket the review")
        return verdict
    # The LAST review is the decisive one: it is the one that preceded the report.
    agent, before, after = sessions[-1]
    verdict["agent"] = agent
    if not before or not after:
        missing = " and ".join(name for name, have in (("before", before), ("after", after))
                              if not have)
        verdict["reason"] = (f"the managed record has no usable {missing} fingerprint bracketing the "
                             f"review, so the review is not evidenced")
        return verdict
    verdict["fingerprint_before"], verdict["fingerprint_after"] = before, after
    if before == after:
        verdict["proven"] = True
        verdict["reason"] = "the managed fingerprints bracketing the review are equal"
    else:
        verdict["mismatch"] = True
        verdict["reason"] = ("the managed fingerprints bracketing the review differ, so the "
                             "candidate changed while it was being reviewed")
    return verdict


def read_review_records(path):
    """The hook's JSON lines from `path`, in order. A line that will not parse is skipped.

    An unreadable file yields an empty list, which `review_identity` reports as unproven. It never
    raises: a missing record must block a planned success, not abort the run.
    """
    records = []
    try:
        with open(path, encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    decoded = json.loads(line)
                except ValueError:
                    continue
                if isinstance(decoded, dict):
                    records.append(decoded)
    except OSError:
        return []
    return records
