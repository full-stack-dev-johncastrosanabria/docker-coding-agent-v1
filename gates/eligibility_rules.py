"""Eligibility rule checker for gates/eligibility.json (tasks.md T005).

Standard library only. The JSON Schema (gates/eligibility.schema.json) guarantees
*soundness*: a recorded eligibility flag never exceeds its evidence. This module
also guarantees *completeness*: every recorded flag must equal the flag computed
from the gate statuses, so a document can neither over-claim nor under-claim.

Rules (tasks.md T005, research.md gate table):
- available: the backend's availability gate passed (Claude: G1a; Codex: G3,
  including its approval-pipeline check).
- trusted_eligible: available, every common gate PASS, the backend's final G11
  PASS (PARTIAL doesn't count), its own production conformance PASS, and its
  trusted gates PASS (Claude: G1c, G1d).
- untrusted_eligible: trusted_eligible plus the secret-unreadability gate
  (Claude: G1b; Codex: G2) and that backend's G9 PASS.
- One backend's statuses never affect the other's eligibility; a common-gate
  failure makes both ineligible.
- A trusted-eligible backend has non-null pins (REQUIRED_PINS).

Pin binding (check_binding): the document is bound to the runtime/versions.yaml
the review ran against. Its runtime_versions_digest must equal the canonical
digest of the current file, and its explicit pinned_versions must equal the
pins extracted from it. Any difference, including a changed docker-agent
artifact SHA-256 or sandbox base, makes the document stale. runtime/versions.yaml
is JSON-compatible YAML (tasks.md, Global constraints), read with json.

Gate-evidence provenance (check_evidence): each gates/<ID>.json records the
environment it actually proved, and stays usable only while that still matches
the current runtime/versions.yaml:
- G0: its recorded pins (G0_PINS) equal the file's. There is no digest, because
  G6 still adds pins after G0.
- G6: its recorded pins (G6_PINS) equal the file's, and its digest (taken from
  the post-G6 file) equals the current digest.
- Every other document: its digest (taken from the file in force when the gate
  ran) equals the current digest.
Anything else is stale: the gate must be re-run. A stale gate is never counted
as PASS or turned into FAIL.

Review (check_review): eligibility may be recomputed only from evidence that is
still valid for the current environment. A document passes only if it is
consistent, bound to the current versions file, every evidence document is
current, and every recorded status equals its evidence (missing evidence counts
as NOT-RUN). Re-binding old evidence to new pins is therefore rejected.

Usage: python3 gates/eligibility_rules.py <eligibility.json> [<runtime/versions.yaml> [<evidence-dir>]]
Exits 0 when the document is consistent (and, given a versions file, bound to
it; and, given the evidence directory, derived from current evidence), 1
otherwise, printing every problem; 2 on a usage error.
"""

import hashlib
import json
import os
import sys

COMMON_GATES = ("G0", "G4", "G5", "G6", "G7", "G8", "G10")

BACKEND_RULES = {
    "claude": {
        "availability": "G1a",
        "trusted": ("G1c", "G1d"),
        "untrusted": ("G1b", "G9"),
    },
    "codex": {
        "availability": "G3",
        "trusted": (),
        "untrusted": ("G2", "G9"),
    },
}

PASS = "PASS"

# Gates judged per backend: the eligibility status comes from evidence backends.<name>.
PER_BACKEND_GATES = ("G9", "G11")

# Every evidence document the review may read, as gates/<ID>.json.
EVIDENCE_IDS = (
    "G0", "G1a", "G1b", "G1c", "G1d", "G2", "G3", "G4", "G5", "G6", "G7", "G8", "G9", "G10", "G11",
    "INVENTORY", "PRODUCTION-CONFORMANCE",
)

# Evidence provenance pins, compared field by field with runtime/versions.yaml.
G0_PINS = ("docker_agent", "docker_agent_config_version", "sbx", "claude_code")
G6_PINS = (
    "docker_agent_artifact_sha256",
    "sandbox_bases.claude.base",
    "sandbox_bases.claude.version",
    "sandbox_bases.codex.base",
    "sandbox_bases.codex.version",
)

MISSING = object()

# pinned_versions field -> path in runtime/versions.yaml.
PIN_SOURCES = {
    "docker_agent": ("docker_agent",),
    "docker_agent_config_version": ("docker_agent_config_version",),
    "docker_agent_artifact_sha256": ("docker_agent_artifact", "sha256"),
    "sbx": ("sbx", "exact"),
    "claude_code": ("claude_code", "exact"),
    "sandbox_bases.claude.base": ("sandbox_bases", "claude", "base"),
    "sandbox_bases.claude.version": ("sandbox_bases", "claude", "version"),
    "sandbox_bases.codex.base": ("sandbox_bases", "codex", "base"),
    "sandbox_bases.codex.version": ("sandbox_bases", "codex", "version"),
}

# Pins that must be non-null before a backend can be trusted-eligible.
REQUIRED_PINS = {
    "claude": (
        "docker_agent_artifact_sha256",
        "sbx",
        "claude_code",
        "sandbox_bases.claude.base",
        "sandbox_bases.claude.version",
    ),
    "codex": (
        "docker_agent_artifact_sha256",
        "sbx",
        "sandbox_bases.codex.base",
        "sandbox_bases.codex.version",
    ),
}


def canonical_json(obj):
    """The canonical JSON text that digests are computed over."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def runtime_versions_digest(versions):
    """'sha256:' + hex SHA-256 of the canonical JSON of the whole versions document."""
    return "sha256:" + hashlib.sha256(canonical_json(versions).encode("utf-8")).hexdigest()


def _lookup(obj, path, default=None):
    for key in path:
        if not isinstance(obj, dict) or key not in obj:
            return default
        obj = obj[key]
    return obj


def _show(value):
    return "missing" if value is MISSING else repr(value)


def pinned_versions(versions):
    """The explicit pinned_versions object for eligibility.json, taken from runtime/versions.yaml."""
    pins = {
        field: _lookup(versions, path)
        for field, path in PIN_SOURCES.items()
        if not field.startswith("sandbox_bases.")
    }
    pins["sandbox_bases"] = {
        backend: {
            "base": _lookup(versions, ("sandbox_bases", backend, "base")),
            "version": _lookup(versions, ("sandbox_bases", backend, "version")),
        }
        for backend in ("claude", "codex")
    }
    return pins


def compute(doc):
    """Return {backend: {available, trusted_eligible, untrusted_eligible}}."""
    common_ok = all(doc["common_gates"].get(g) == PASS for g in COMMON_GATES)
    result = {}
    for name, rules in BACKEND_RULES.items():
        backend = doc["backends"][name]
        status = backend["gate_status"]
        available = status.get(rules["availability"]) == PASS
        trusted = (
            available
            and common_ok
            and status.get("G11") == PASS
            and backend.get("production_conformance") == PASS
            and all(status.get(g) == PASS for g in rules["trusted"])
        )
        untrusted = trusted and all(status.get(g) == PASS for g in rules["untrusted"])
        result[name] = {
            "available": available,
            "trusted_eligible": trusted,
            "untrusted_eligible": untrusted,
        }
    return result


def check(doc):
    """Return a list of human-readable problems; an empty list means consistent."""
    problems = []
    computed = compute(doc)
    for name, expected in computed.items():
        backend = doc["backends"][name]
        for flag, value in expected.items():
            if backend.get(flag) is not value:
                problems.append(
                    f"{name}.{flag} is {backend.get(flag)!r} but the gate statuses give {value!r}"
                )
        if not expected["available"]:
            if not backend.get("unavailable_reason"):
                problems.append(f"{name} is unavailable but has no unavailable_reason")
            if backend.get("production_conformance") != "NOT-APPLICABLE":
                problems.append(
                    f"{name} is unavailable but production_conformance is "
                    f"{backend.get('production_conformance')!r}, not 'NOT-APPLICABLE'"
                )
        if backend.get("trusted_eligible") is True or expected["trusted_eligible"]:
            for field in REQUIRED_PINS[name]:
                if _lookup(doc.get("pinned_versions"), field.split(".")) is None:
                    problems.append(f"{name} is trusted-eligible but pinned_versions.{field} is null")
    if doc["common_gates"].get("G4") == PASS and not doc.get("network_policy_fingerprint"):
        problems.append("G4 passed but network_policy_fingerprint is missing")
    return problems


def check_binding(doc, versions):
    """Return the problems that make doc stale against versions; an empty list means bound."""
    problems = []
    digest = runtime_versions_digest(versions)
    if doc.get("runtime_versions_digest") != digest:
        problems.append(
            f"stale: runtime_versions_digest is {doc.get('runtime_versions_digest')!r} "
            f"but runtime/versions.yaml gives {digest!r}"
        )
    recorded = doc.get("pinned_versions")
    for field, path in PIN_SOURCES.items():
        want = _lookup(versions, path)
        have = _lookup(recorded, field.split("."), MISSING)
        if have is MISSING or have != want:
            problems.append(
                f"stale: pinned_versions.{field} is {_show(have)} "
                f"but runtime/versions.yaml pins {want!r}"
            )
    return problems


def evidence_provenance(gate, versions):
    """The provenance gates/<gate>.json records, from the runtime/versions.yaml in force when it runs."""
    if gate == "G0":
        return {"pins": {field: _lookup(versions, PIN_SOURCES[field]) for field in G0_PINS}}
    digest = runtime_versions_digest(versions)
    if gate == "G6":
        pins = pinned_versions(versions)
        return {
            "pins": {
                "docker_agent_artifact_sha256": pins["docker_agent_artifact_sha256"],
                "sandbox_bases": pins["sandbox_bases"],
            },
            "runtime_versions_digest": digest,
        }
    return {"runtime_versions_digest": digest}


def evidence_problems(evidence, versions):
    """Return the problems that make one gate evidence document stale against versions."""
    gate = evidence.get("gate")
    provenance = evidence.get("provenance")
    if not isinstance(provenance, dict):
        return [f"{gate}: stale: no provenance"]
    problems = []
    pins = G0_PINS if gate == "G0" else G6_PINS if gate == "G6" else ()
    for field in pins:
        want = _lookup(versions, PIN_SOURCES[field])
        have = _lookup(provenance.get("pins"), field.split("."), MISSING)
        if have is MISSING or have != want:
            problems.append(
                f"{gate}: stale: provenance.pins.{field} is {_show(have)} "
                f"but runtime/versions.yaml pins {want!r}"
            )
    if gate != "G0":
        digest = runtime_versions_digest(versions)
        have = provenance.get("runtime_versions_digest", MISSING)
        if have != digest:
            problems.append(
                f"{gate}: stale: provenance.runtime_versions_digest is {_show(have)} "
                f"but runtime/versions.yaml gives {digest!r}"
            )
    return problems


def check_evidence(evidence_by_id, versions):
    """Problems for every evidence document that isn't current; an empty list means all current."""
    problems = []
    for evidence_id, evidence in sorted(evidence_by_id.items()):
        if evidence.get("gate") != evidence_id:
            problems.append(f"gates/{evidence_id}.json records gate {evidence.get('gate')!r}")
            continue
        problems += evidence_problems(evidence, versions)
    return problems


def _evidence_status(evidence_by_id, gate, backend=None):
    evidence = evidence_by_id.get(gate)
    if evidence is None:
        return "NOT-RUN"
    if backend is not None:
        return _lookup(evidence, ("backends", backend, "status"))
    return evidence.get("status")


def check_review(doc, evidence_by_id, versions):
    """Problems that stop doc from being a valid review of the current evidence and versions.

    Empty only when doc is consistent, bound to versions, every evidence document is
    current, and every recorded status equals its evidence.
    """
    problems = check(doc) + check_binding(doc, versions) + check_evidence(evidence_by_id, versions)
    for gate in COMMON_GATES:
        want = _evidence_status(evidence_by_id, gate)
        have = doc["common_gates"].get(gate)
        if have != want:
            problems.append(f"common_gates.{gate} is {have!r} but its evidence gives {want!r}")
    for name in BACKEND_RULES:
        backend = doc["backends"][name]
        for gate, have in backend["gate_status"].items():
            want = _evidence_status(evidence_by_id, gate, name if gate in PER_BACKEND_GATES else None)
            if have != want:
                problems.append(f"{name}.gate_status.{gate} is {have!r} but its evidence gives {want!r}")
        if backend.get("available"):
            want = _evidence_status(evidence_by_id, "PRODUCTION-CONFORMANCE", name)
            if backend.get("production_conformance") != want:
                problems.append(
                    f"{name}.production_conformance is {backend.get('production_conformance')!r} "
                    f"but its evidence gives {want!r}"
                )
    return problems


def load_evidence(directory):
    """Read every gates/<ID>.json present in directory, keyed by ID."""
    evidence_by_id = {}
    for evidence_id in EVIDENCE_IDS:
        path = os.path.join(directory, f"{evidence_id}.json")
        if os.path.exists(path):
            with open(path, encoding="utf-8") as fh:
                evidence_by_id[evidence_id] = json.load(fh)
    return evidence_by_id


def main(argv):
    if len(argv) not in (2, 3, 4):
        print(
            "usage: python3 gates/eligibility_rules.py <eligibility.json> "
            "[<runtime/versions.yaml> [<evidence-dir>]]",
            file=sys.stderr,
        )
        return 2
    with open(argv[1], encoding="utf-8") as fh:
        doc = json.load(fh)
    if len(argv) == 2:
        problems = check(doc)
    else:
        with open(argv[2], encoding="utf-8") as fh:
            versions = json.load(fh)
        if len(argv) == 3:
            problems = check(doc) + check_binding(doc, versions)
        else:
            problems = check_review(doc, load_evidence(argv[3]), versions)
    for problem in problems:
        print(f"eligibility: {problem}")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
