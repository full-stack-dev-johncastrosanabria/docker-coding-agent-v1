"""Host-authoritative approval grants (tasks.md T036, data-model ApprovalGrant).

Standard library only. This module MANUFACTURES grants from an authoritative prior report. It never
reads one, and there is deliberately no function that could: V1 has no manually authored grant-file
input, and the single trusted approval channel is `dca run --approve <request-id>`.

WHY THAT ASYMMETRY IS THE WHOLE POINT. A grant is the only thing that turns an ASK into an ALLOW.
If a grant could be supplied as input, repository content or a compromised in-VM process could
write one and approve itself. So every provenance field is computed here on the host from the prior
report - `origin_report_digest` is the SHA-256 of the host copy - and none of them can be produced
by anything inside the VM. The in-VM copy of the grants is only a hint for the cooperative gate;
forging it grants nothing that matters, because network grants reach the sandbox through the
host-applied sbx policy and reported approvals are reconciled against the host copy.

Every refusal is exit 3, "stale or undiscoverable approval", because the developer's remedy is
always the same: re-run and approve again.
"""

import hashlib
import json
import os
import re

# data-model Action Classes: these are never grantable, whatever a report says.
NON_GRANTABLE = frozenset({2, 5, 16, 19, 22, 23, 25, 26, 27, 29, 30})

GRANTABLE_STATUS = frozenset({"requested", "unanswered"})

# `apr-<origin-run-id>-<n>`; the origin run id is encoded in the request id, which is how the
# launcher locates the authoritative report without a global approval database.
_REQUEST_ID = re.compile(r"\Aapr-(run-[0-9TZ:.-]+-[0-9a-f]{6})-([0-9]+)\Z")

DEFAULT_RUNS_DIRNAME = ".dca-runs"
GRANTED_BY = "developer-cli"


class GrantError(Exception):
    """A stale or undiscoverable approval. The launcher maps this to exit 3."""

    exit_code = 3


def origin_run_id(request_id):
    """The originating run id encoded in a request id."""
    if not isinstance(request_id, str):
        raise GrantError(f"request id {request_id!r} is not a string")
    match = _REQUEST_ID.match(request_id)
    if not match:
        raise GrantError(f"request id {request_id!r} is malformed")
    return match.group(1)


def default_report_path(repo_root, origin):
    """`<repo>/../.dca-runs/<origin-run-id>/report.json`."""
    parent = os.path.dirname(os.path.abspath(str(repo_root)))
    return os.path.join(parent, DEFAULT_RUNS_DIRNAME, origin, "report.json")


def _locate(repo_root, origin, approval_report):
    path = str(approval_report) if approval_report else default_report_path(repo_root, origin)
    if not os.path.isfile(path):
        raise GrantError(
            f"the originating report for {origin} was not found at {path}; "
            "pass --approval-report if the origin run used a custom --out")
    return path


def _read_report(path, origin, expected_digest=None):
    """(document, digest). The digest is of the bytes on disk, which is what binds the grant."""
    try:
        with open(path, "rb") as fh:
            raw = fh.read()
        document = json.loads(raw.decode("utf-8"))
    except (OSError, ValueError, UnicodeDecodeError) as exc:
        raise GrantError(f"the originating report at {path} is unreadable: {exc}") from exc
    if not isinstance(document, dict):
        raise GrantError(f"the originating report at {path} is not an object")
    if document.get("run_id") != origin:
        raise GrantError(
            f"the report at {path} records run {document.get('run_id')!r}, not {origin!r}")
    digest = "sha256:" + hashlib.sha256(raw).hexdigest()
    if expected_digest is not None and digest != expected_digest:
        raise GrantError(
            f"the report digest {digest} does not match the expected {expected_digest}")
    return document, digest


def _find_request(document, request_id):
    for entry in document.get("approvals") or []:
        if isinstance(entry, dict) and entry.get("id") == request_id:
            return entry
    raise GrantError(f"request {request_id} is not in the originating report")


def _scope(request):
    """The grant's scope: the request's own normalized target or declared equivalence class."""
    target = request.get("normalized_target")
    if isinstance(target, str) and target:
        return {"exact": target}
    equivalence = request.get("equivalence_class")
    if isinstance(equivalence, dict) and equivalence.get("action_type") \
            and isinstance(equivalence.get("targets"), list) and equivalence["targets"]:
        return {"equivalence_class": {"action_type": equivalence["action_type"],
                                      "targets": list(equivalence["targets"])}}
    raise GrantError(
        f"request {request.get('id')} declares neither a normalized target nor an "
        "equivalence class, so a grant cannot be scoped")


def _check_same_task(document, new_run, request_id):
    """The new run must be the SAME task, or the approval is stale."""
    origin_source = document.get("source") or {}
    checks = (
        ("source_commit", origin_source.get("commit"), new_run.get("source_commit")),
        ("task_fingerprint", document.get("task_fingerprint"), new_run.get("task_fingerprint")),
        ("trust_level", document.get("trust_level"), new_run.get("trust_level")),
        ("backend", document.get("backend"), new_run.get("backend")),
    )
    for field, origin_value, new_value in checks:
        if origin_value != new_value:
            raise GrantError(
                f"{field} changed since {request_id} was requested "
                f"({origin_value!r} -> {new_value!r}), so the approval is stale")


def create_grants(*, request_ids, new_run, repo_root, approval_report=None,
                  expected_report_digest=None):
    """The grant document for `new_run`, built from the authoritative prior report(s).

    Raises GrantError (exit 3) on anything stale or undiscoverable. Nothing here accepts a grant
    as input: every field is computed from the report on the host.
    """
    if not request_ids:
        raise GrantError("no approval request ids were given")

    built = []
    for request_id in request_ids:
        origin = origin_run_id(request_id)
        path = _locate(repo_root, origin, approval_report)
        document, digest = _read_report(path, origin, expected_report_digest)

        request = _find_request(document, request_id)
        status = request.get("status")
        if status not in GRANTABLE_STATUS:
            raise GrantError(
                f"request {request_id} has status {status!r}; only "
                f"{sorted(GRANTABLE_STATUS)} may be granted")

        action_class = request.get("action_class")
        if not isinstance(action_class, int) or not 1 <= action_class <= 31:
            raise GrantError(
                f"request {request_id} has action class {action_class!r}, which is not 1-31")
        if action_class in NON_GRANTABLE:
            raise GrantError(
                f"action class {action_class} is a DENY class and is never grantable")

        _check_same_task(document, new_run, request_id)

        built.append({
            "request_id": request_id,
            "action_class": action_class,
            "scope": _scope(request),
            "provenance": {
                "origin_run_id": origin,
                "origin_report_digest": digest,
                "task_fingerprint": document.get("task_fingerprint"),
                "source_commit": (document.get("source") or {}).get("commit"),
                "origin_backend": document.get("backend"),
                "origin_trust_level": document.get("trust_level"),
            },
        })

    return {"run_id": new_run.get("run_id"), "granted_by": GRANTED_BY, "grants": built}
