"""Host-side gate-evidence checks for the launcher (tasks.md T064, precondition 7).

Standard library only, and that constraint shapes the whole module: the runtime never imports
`jsonschema`, so `gates/eligibility.json` is validated with `dca.jsonschema` against the SAME
`gates/eligibility.schema.json` the gate tooling uses. The test corpus (T063) runs both validators
over every fixture and generated variant and requires them to agree, so "stdlib validator" does not
quietly mean "weaker validator".

WHAT THIS FILE IS ALLOWED TO READ, AND WHAT IT IS NOT. It reads exactly two host files:
`gates/eligibility.json` and `runtime/versions.yaml`. It never reads evidence from the VM, from the
repository under test, or from anything the agent produced. Gate evidence that came out of a
sandbox would be evidence about a sandbox, offered by the thing it is supposed to constrain.

STALENESS IS NOT FAILURE, AND NEITHER IS SILENCE. A document whose `runtime_versions_digest` or
explicit pins no longer match `runtime/versions.yaml` describes an environment that no longer
exists. It is refused - the gates must be re-run - and it is never reinterpreted as a FAIL, and
never quietly accepted because the parts it does mention still look right.
"""

import json
import os

_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.dirname(os.path.dirname(_HERE))

try:
    from . import jsonschema
except ImportError:  # loaded by path in tests and in the sandbox
    import importlib.util as _ilu
    import sys as _sys

    jsonschema = _sys.modules.get("dca_jsonschema")
    if jsonschema is None:
        _spec = _ilu.spec_from_file_location("dca_jsonschema",
                                             os.path.join(_HERE, "jsonschema.py"))
        jsonschema = _ilu.module_from_spec(_spec)
        _sys.modules["dca_jsonschema"] = jsonschema
        _spec.loader.exec_module(jsonschema)

ELIGIBILITY_PATH = os.path.join(_REPO_ROOT, "gates", "eligibility.json")
SCHEMA_PATH = os.path.join(_REPO_ROOT, "gates", "eligibility.schema.json")
VERSIONS_PATH = os.path.join(_REPO_ROOT, "runtime", "versions.yaml")

#: Gates that are backend-independent. Any one of them not PASS stops every run of every profile.
COMMON_GATES = ("G0", "G4", "G5", "G6", "G7", "G8", "G10")
#: Per backend: the gate that says it exists, and the gates a trusted run additionally needs.
AVAILABILITY_GATE = {"claude": "G1a", "codex": "G3"}
TRUSTED_GATES = {"claude": ("G1c", "G1d"), "codex": ()}
#: The secret-unreadability gate an untrusted run additionally needs, alongside G9.
UNTRUSTED_GATES = {"claude": ("G1b", "G9"), "codex": ("G2", "G9")}

PASS = "PASS"


class EvidenceProblem(Exception):
    """The gate evidence cannot support a run. The launcher maps this to exit 3."""


def load_schema(path=None):
    with open(path or SCHEMA_PATH, encoding="utf-8") as handle:
        return json.load(handle)


def load_versions(path=None):
    with open(path or VERSIONS_PATH, encoding="utf-8") as handle:
        return json.load(handle)


def canonical_digest(versions):
    """The canonical digest of the whole `runtime/versions.yaml` (tasks.md, Global constraints)."""
    import hashlib
    body = json.dumps(versions, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return "sha256:" + hashlib.sha256(body.encode("utf-8")).hexdigest()


def structural_problems(document, schema=None):
    """Schema conformance, decided by the stdlib validator. Never imports `jsonschema`."""
    return jsonschema.validate(document, schema or load_schema())


def load(path=None, schema=None):
    """Read and structurally validate `gates/eligibility.json`, or raise `EvidenceProblem`."""
    path = path or ELIGIBILITY_PATH
    if not os.path.isfile(path):
        raise EvidenceProblem(
            f"required gate evidence is missing: {path}. Run the gate procedures, then "
            "gates/review.py, before running dca.")
    try:
        with open(path, encoding="utf-8") as handle:
            document = json.load(handle)
    except ValueError as exc:
        raise EvidenceProblem(f"{path} is not valid JSON: {exc}") from exc
    problems = structural_problems(document, schema)
    if problems:
        raise EvidenceProblem(
            f"{path} does not conform to gates/eligibility.schema.json:\n  "
            + "\n  ".join(problems[:10]))
    return document


def check_freshness(document, versions=None, path=None):
    """The evidence must describe the environment that is installed right now.

    Both halves matter. The canonical digest catches ANY change to `runtime/versions.yaml`,
    including one to a field the document does not restate; the explicit `pinned_versions`
    comparison catches a document that carries the right digest but disagrees about a pin it does
    name. Either mismatch means the gates were run somewhere else.
    """
    versions = versions or load_versions()
    expected = canonical_digest(versions)
    recorded = document.get("runtime_versions_digest")
    if recorded != expected:
        raise EvidenceProblem(
            f"{path or ELIGIBILITY_PATH} is stale: it was computed for runtime_versions_digest "
            f"{recorded}, but runtime/versions.yaml is now {expected}. Re-run the gates and "
            "gates/review.py.")
    pinned = document.get("pinned_versions") or {}
    mismatches = []
    for key, path_in_versions in (("docker_agent", ("docker_agent",)),
                                  ("docker_agent_config_version", ("docker_agent_config_version",)),
                                  ("docker_agent_artifact_sha256",
                                   ("docker_agent_artifact", "sha256")),
                                  ("sbx", ("sbx", "exact")),
                                  ("claude_code", ("claude_code", "exact"))):
        current = versions
        for part in path_in_versions:
            current = (current or {}).get(part) if isinstance(current, dict) else None
        if key in pinned and pinned[key] != current:
            mismatches.append(f"{key}: evidence {pinned[key]!r}, installed pin {current!r}")
    bases = pinned.get("sandbox_bases") or {}
    for backend, base in sorted(bases.items()):
        current = (versions.get("sandbox_bases") or {}).get(backend) or {}
        for field in ("base", "version"):
            if base.get(field) != current.get(field):
                mismatches.append(
                    f"sandbox_bases.{backend}.{field}: evidence {base.get(field)!r}, "
                    f"installed pin {current.get(field)!r}")
    if mismatches:
        raise EvidenceProblem(
            "the gate evidence was computed for other pinned versions:\n  "
            + "\n  ".join(mismatches))


def check_common_gates(document):
    common = document.get("common_gates") or {}
    failing = [gate for gate in COMMON_GATES if common.get(gate) != PASS]
    if failing:
        detail = ", ".join(f"{gate}={common.get(gate)!r}" for gate in failing)
        extra = ""
        if "G4" in failing:
            extra = (" G4 is the effective network policy: no run of any profile proceeds until "
                     "it passes.")
        raise EvidenceProblem(
            f"a backend-independent gate has not passed: {detail}.{extra}")


def backend_entry(document, backend):
    entry = (document.get("backends") or {}).get(backend)
    if not isinstance(entry, dict):
        raise EvidenceProblem(f"the gate evidence records nothing about backend {backend!r}")
    return entry


def check_backend_available(document, backend):
    entry = backend_entry(document, backend)
    if entry.get("available") is not True:
        reason = entry.get("unavailable_reason") or (
            f"its availability gate {AVAILABILITY_GATE.get(backend)} did not pass")
        raise EvidenceProblem(f"backend {backend!r} is not available: {reason}")
    return entry


def check_backend_runnable(document, backend):
    """Precondition 7 for the SELECTED backend: available, trusted-eligible, G11 final, conformant.

    `trusted_eligible` is required even for an untrusted request, because it is the floor: it means
    the backend's own gates passed, its G11 is a FINAL pass rather than part A only, and its
    production assets were conformance-checked. Untrusted adds gates on top of that floor; it never
    substitutes for it. A missing UNTRUSTED gate is deliberately not handled here - that is a
    Phase 2 `blocked` report, not a precondition failure.
    """
    entry = check_backend_available(document, backend)
    status = entry.get("gate_status") or {}
    problems = []
    if status.get("G11") != PASS:
        problems.append(
            f"its G11 status is {status.get('G11')!r}; a PARTIAL part-A-only status does not count")
    if entry.get("production_conformance") != PASS:
        problems.append(
            f"its production conformance is {entry.get('production_conformance')!r}, not PASS")
    for gate in TRUSTED_GATES.get(backend, ()):
        if status.get(gate) != PASS:
            problems.append(f"its trusted-profile gate {gate} is {status.get(gate)!r}")
    if entry.get("trusted_eligible") is not True and not problems:
        problems.append("gates/eligibility.json records trusted_eligible: false")
    if problems:
        raise EvidenceProblem(
            f"backend {backend!r} cannot be run: " + "; ".join(problems))
    return entry


def untrusted_blockers(document, backend):
    """The gates an UNTRUSTED run needs that have not passed. Empty means it may proceed.

    This returns rather than raises on purpose: a missing untrusted gate is the S5a case - a valid
    request whose correct policy outcome is `blocked`, with a report and exit 11 - not a refusal.
    """
    entry = backend_entry(document, backend)
    status = entry.get("gate_status") or {}
    missing = [gate for gate in UNTRUSTED_GATES.get(backend, ())
               if status.get(gate) != PASS]
    if entry.get("untrusted_eligible") is not True and not missing:
        missing.append("untrusted_eligible")
    return missing


def network_policy_fingerprint(document):
    return document.get("network_policy_fingerprint")


def credential_mechanism(document, backend):
    return backend_entry(document, backend).get("credential_mechanism")
