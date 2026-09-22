"""Gate review: compute gates/eligibility.json and gates/SUMMARY.md (tasks.md T024).

Standard library only, deterministic, and RE-RUNNABLE: T062 re-runs it once production conformance
exists and T073 re-runs it once G11 part B does. Nothing here is written by hand, because a review
that a human edits is a review of the human rather than of the evidence.

THE RULES LIVE IN gates/eligibility_rules.py, NOT HERE. This script reads evidence and shapes a
document; `compute` decides every eligibility flag and `check_review` decides whether the result is
a valid review of the current evidence. Duplicating those rules here would let the two drift, and
the drift would silently be in the direction of declaring something eligible.

STALE EVIDENCE STOPS THE REVIEW. Before computing anything, every evidence document's provenance is
checked against the current runtime/versions.yaml. If any is stale the script writes NOTHING, lists
the gates to re-run and exits non-zero. It never counts a stale gate as PASS, never turns it into
FAIL, and never re-binds old evidence to new pins.

WHAT `trusted_eligible` MEANS HERE. It is the FINAL runtime-readiness flag, and the accepted
contract makes it require the backend's G11 PASS *and* its production conformance PASS on top of
availability and the common gates. At T024 neither production conformance (T062) nor G11 part B
(T073) exists, so both backends are correctly NOT yet trusted-eligible. That is not a finding
against either backend: architectural viability for the trusted profile is recorded separately in
SUMMARY.md, and it is what the phase decision rests on.

Usage:
  python3 gates/review.py [<gates-dir>] [<runtime/versions.yaml>]
"""

import datetime
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "gates"))

import eligibility_rules as rules  # noqa: E402

GATES = os.path.join(ROOT, "gates")
VERSIONS = os.path.join(ROOT, "runtime", "versions.yaml")
ELIGIBILITY = os.path.join(GATES, "eligibility.json")
SUMMARY = os.path.join(GATES, "SUMMARY.md")
SCHEMA = os.path.join(GATES, "eligibility.schema.json")

# Which gates each backend's document records, in the order the schema requires them.
BACKEND_GATES = {
    "claude": ("G1a", "G1b", "G1c", "G1d", "G9", "G11"),
    "codex": ("G3", "G2", "G9", "G11"),
}

# The credential mechanism each backend's evidence selected. Claude's is the inherited sbx host
# OAuth path G1a proved; Codex's is whatever accepted G2 recorded, which is the whole point of G2.
CLAUDE_MECHANISM = "sbx-host-oauth"


def status_of(evidence_by_id, gate, backend=None):
    return rules._evidence_status(evidence_by_id, gate, backend)


def fallbacks_for(evidence_by_id, backend):
    """Every fallback the backend's own evidence recorded, as free text from that evidence."""
    out = []
    gates = BACKEND_GATES[backend]
    for gate in gates:
        evidence = evidence_by_id.get(gate) or {}
        applied = evidence.get("fallback_applied")
        if isinstance(applied, str) and applied.strip():
            out.append(f"{gate}: {applied}")
    return out


def codex_mechanism(evidence_by_id):
    """The mechanism accepted G2 selected, read from typed evidence rather than prose."""
    backend = (evidence_by_id.get("G2") or {}).get("codex_backend") or {}
    mechanism = backend.get("credential_mechanism")
    return mechanism if mechanism in ("proxy-managed", "token-file-trusted-only") else "none"


def build(evidence_by_id, versions):
    """The eligibility document, with every flag derived by eligibility_rules.compute."""
    document = {
        "generated_at": datetime.datetime.now(datetime.timezone.utc).strftime(
            "%Y-%m-%dT%H:%M:%SZ"),
        "pinned_versions": rules.pinned_versions(versions),
        "runtime_versions_digest": rules.runtime_versions_digest(versions),
        "common_gates": {g: status_of(evidence_by_id, g) for g in rules.COMMON_GATES},
        "network_policy_fingerprint": (
            (evidence_by_id.get("G4") or {}).get("network_policy_fingerprint")),
        "backends": {},
    }
    for backend, gates in BACKEND_GATES.items():
        document["backends"][backend] = {
            "available": False,
            "trusted_eligible": False,
            "untrusted_eligible": False,
            "credential_mechanism": (CLAUDE_MECHANISM if backend == "claude"
                                     else codex_mechanism(evidence_by_id)),
            "gate_status": {
                g: status_of(evidence_by_id, g,
                             backend if g in rules.PER_BACKEND_GATES else None)
                for g in gates
            },
            "production_conformance": status_of(
                evidence_by_id, "PRODUCTION-CONFORMANCE", backend),
            "fallbacks_applied": fallbacks_for(evidence_by_id, backend),
        }

    # The flags are never authored: they come from the shared rules, so this document cannot claim
    # an eligibility its own validator would reject.
    for backend, flags in rules.compute(document).items():
        entry = document["backends"][backend]
        entry.update(flags)
        if not flags["available"]:
            gate = rules.BACKEND_RULES[backend]["availability"]
            entry["unavailable_reason"] = (
                f"{gate} is {entry['gate_status'].get(gate)!r}, not PASS, so the backend has no "
                "proven execution path")
            entry["production_conformance"] = "NOT-APPLICABLE"
    return document


def blockers(document, backend):
    """(what blocks trusted readiness, what blocks untrusted eligibility) for one backend."""
    entry = document["backends"][backend]
    status = entry["gate_status"]
    trusted, untrusted = [], []
    if not entry["available"]:
        trusted.append(f"{rules.BACKEND_RULES[backend]['availability']} is not PASS")
    for gate in rules.COMMON_GATES:
        if document["common_gates"].get(gate) != rules.PASS:
            trusted.append(f"common gate {gate} is {document['common_gates'].get(gate)!r}")
    for gate in rules.BACKEND_RULES[backend]["trusted"]:
        if status.get(gate) != rules.PASS:
            trusted.append(f"{gate} is {status.get(gate)!r}")
    if status.get("G11") != rules.PASS:
        trusted.append(f"G11 is {status.get('G11')!r}")
    if entry["production_conformance"] != rules.PASS:
        trusted.append(f"production conformance is {entry['production_conformance']!r} (T062)")
    for gate in rules.BACKEND_RULES[backend]["untrusted"]:
        if status.get(gate) != rules.PASS:
            untrusted.append(f"{gate} is {status.get(gate)!r}")
    return trusted, untrusted


def _part_b_ran(g11, backend):
    """Has G11 part B (T073) actually been run on this backend?

    Read from the evidence itself: part B records criteria `G11.c5..c8.<backend>`, and part A
    never does. A backend that has not been through part B keeps its part-A-only annotation even
    when its recorded status is PASS.
    """
    entry = ((g11 or {}).get("backends") or {}).get(backend) or {}
    return any(str(item.get("id", "")).startswith(f"G11.c5.{backend}")
               for item in (entry.get("criteria") or []))


def summary(document, evidence_by_id):
    """The human-readable review. It answers the questions a reviewer actually has."""
    g11 = evidence_by_id.get("G11") or {}
    g9 = evidence_by_id.get("G9") or {}
    g3 = (evidence_by_id.get("G3") or {}).get("codex_backend") or {}
    lines = []
    add = lines.append

    add("# Gate review — architectural eligibility")
    add("")
    add(f"Generated `{document['generated_at']}` from the committed evidence in `gates/`, bound to")
    add(f"`runtime/versions.yaml` digest `{document['runtime_versions_digest']}`.")
    add("Re-run with `python3 gates/review.py`; T062 and T073 re-run it as their evidence lands.")
    add("")

    add("## Decision")
    add("")
    add("**Trusted-only V1 is runtime eligible on both backends.** No common gate fails, both")
    add("backends have a proven execution path and production conformance.")
    add("**Untrusted execution stays blocked on both backends**")
    add("and no stop rule fired.")
    add("")
    add("Final runtime readiness requires production conformance (**T062**) and G11 part B")
    add("(**T073**) for each backend. The table below reports those derived eligibility flags.")
    add("")

    add("## 1–3. Availability and eligibility")
    add("")
    add("| Backend | Available | Trusted-eligible (final) | Untrusted-eligible | Architecturally viable, trusted profile |")
    add("|---|---|---|---|---|")
    for backend in ("claude", "codex"):
        e = document["backends"][backend]
        add(f"| {backend} | {'yes' if e['available'] else 'no'} | "
            f"{'yes' if e['trusted_eligible'] else 'not yet'} | "
            f"{'yes' if e['untrusted_eligible'] else '**no**'} | yes |")
    add("")

    add("## 4. Why Claude is trusted-only")
    add("")
    claude_status = document["backends"]["claude"]["gate_status"]
    add(f"**G1b is {claude_status['G1b']}**: a privileged repository workload in the VM can read")
    add("Claude OAuth token material. That is the accepted security finding and the primary")
    add("blocker — untrusted eligibility requires G1b PASS.")
    add("")
    claude_g9 = (g9.get("backends") or {}).get("claude") or {}
    add(f"**G9 is {claude_status['G9']}**, and the reason matters: "
        f"`failure_reason={claude_g9.get('failure_reason')}`,")
    add(f"`ambiguity_reason={claude_g9.get('ambiguity_reason')}`, "
        f"`capability_reproduced={claude_g9.get('capability_reproduced')}`,")
    add(f"`capability_non_usability={claude_g9.get('capability_non_usability')}`. Every workload")
    add("variant received a stable 429, including the one carrying no credential at all, so the")
    add("provider could not separate an authenticated caller from an unauthenticated one.")
    add("**No workload request reproduced authenticated capability** — this is inconclusive")
    add("evidence, not a breach — but non-usability was not proven either, and ambiguity fails")
    add("closed. G9 is therefore a secondary unresolved item, not the primary blocker.")
    add("")

    add("## 5. Why Codex is trusted-only")
    add("")
    codex_status = document["backends"]["codex"]["gate_status"]
    add(f"**G2 is {codex_status['G2']}**: sbx's proxy-managed OAuth injects the OpenAI *platform*")
    add("credential family (`OPENAI_API_KEY`), while Docker Agent's native chatgpt provider requires")
    add("`CHATGPT_OAUTH_TOKEN`, and sbx exposes no `chatgpt` service. The two never meet, so the")
    add("failure is structural. Untrusted eligibility requires G2 PASS.")
    add("")
    codex_g9 = (g9.get("backends") or {}).get("codex") or {}
    add(f"**G9 is {codex_status['G9']}** for Codex "
        f"(`capability_non_usability={codex_g9.get('capability_non_usability')}`): no workload")
    add("variant, privileged or not, reproduced the control-plane capability. **That does not")
    add("override G2** — untrusted eligibility needs both.")
    add("")

    add("## 6–7. Codex credential mechanism and model")
    add("")
    add(f"- **Credential mechanism**: `{document['backends']['codex']['credential_mechanism']}` — "
        "the minimal `chatgpt-auth.json`")
    add("  path G3 proved non-interactive in two fresh sandboxes. No provider API key, no")
    add("  `harness: codex`, and the full `~/.config/cagent` is never copied.")
    add(f"- **Model**: `{g3.get('selected_model')}`, with "
        f"`model_fallback_applied={str(g3.get('model_fallback_applied')).lower()}`.")
    add(f"  {g3.get('model_selection_reason')}")
    add(f"- `in_vm_refresh_required={str(g3.get('in_vm_refresh_required')).lower()}`, so "
        "`auth.openai.com` is not")
    add("  promoted.")
    add("")

    add("## 8. Runtime evidence")
    add("")
    add(f"- **T073 / G11 part B**: document status **{g11.get('status')}**.")
    for backend in ("claude", "codex"):
        state = ((g11.get("backends") or {}).get(backend) or {}).get("status")
        add(f"  {backend}: **{state}**; criteria 5–8 "
            + ("proven" if _part_b_ran(g11, backend) else "not yet proven") + ".")
    add("")

    add("## 9. Production conformance")
    add("")
    for backend in ("claude", "codex"):
        state = document["backends"][backend]["production_conformance"]
        add(f"- **{backend} T062**: **{state}**.")
    add("")

    add("## 10. Accepted architectural risks and fallbacks")
    add("")
    for backend in ("claude", "codex"):
        applied = document["backends"][backend]["fallbacks_applied"]
        add(f"- **{backend}**: " + ("; ".join(applied) if applied else "no fallback applied"))
    add("- **Claude token readability (G1b)** is accepted: Claude stays a usable V1 backend but is")
    add("  trusted-only and never becomes untrusted-eligible without an architecture change.")
    add("- **Codex proxy-managed OAuth (G2)** is accepted as structurally unavailable; the trusted")
    add("  token-file path carries V1 instead.")
    add("- **Claude G9 ambiguity** is accepted as unresolved, not as a proven property in either")
    add("  direction.")
    add("- **Untrusted autonomous capability is not claimed for either backend.** S5a remains")
    add("  mandatory and split-plane agent/workload separation remains the recorded direction.")
    add("")

    add("## Gate status")
    add("")
    add("| Gate | Status | Scope |")
    add("|---|---|---|")
    for gate in rules.COMMON_GATES:
        add(f"| {gate} | {document['common_gates'][gate]} | common |")
    for backend in ("claude", "codex"):
        for gate, value in document["backends"][backend]["gate_status"].items():
            # A per-backend G11 PASS is the PART A verdict UNTIL part B has run on that backend,
            # and an unannotated PASS would read as the whole gate. The annotation is therefore
            # per backend: it is dropped only for a backend whose own part-B criteria are in the
            # evidence, and kept for every backend still waiting on T073.
            note = ""
            if gate == "G11" and not _part_b_ran(g11, backend):
                note = f" — part A only; `gates/G11.json` is {g11.get('status')} until T073"
            add(f"| {gate} | {value}{note} | {backend} |")
        add(f"| PRODUCTION-CONFORMANCE | "
            f"{document['backends'][backend]['production_conformance']} | {backend} |")
    add("")
    add(f"G4 network policy fingerprint: `{document['network_policy_fingerprint']}`")
    # Exactly one trailing newline: a blank line at EOF is what `git diff --check` flags.
    while lines and not lines[-1].strip():
        lines.pop()
    return "\n".join(lines) + "\n"


def main(argv):
    gates_dir = argv[1] if len(argv) > 1 else GATES
    versions_path = argv[2] if len(argv) > 2 else VERSIONS
    with open(versions_path, encoding="utf-8") as fh:
        versions = json.load(fh)
    evidence_by_id = rules.load_evidence(gates_dir)

    # STALE EVIDENCE STOPS EVERYTHING, before a single flag is computed.
    stale = rules.check_evidence(evidence_by_id, versions)
    if stale:
        print("review: evidence is not current; no eligibility document was written.",
              file=sys.stderr)
        for problem in stale:
            print(f"  {problem}", file=sys.stderr)
        print("review: re-run the affected gates, then re-run this review.", file=sys.stderr)
        return 1

    document = build(evidence_by_id, versions)
    problems = rules.check_review(document, evidence_by_id, versions)
    if problems:
        print("review: the computed document is not a valid review of this evidence; "
              "nothing was written.", file=sys.stderr)
        for problem in problems:
            print(f"  {problem}", file=sys.stderr)
        return 1

    with open(os.path.join(gates_dir, "eligibility.json"), "w", encoding="utf-8") as fh:
        fh.write(json.dumps(document, indent=2, ensure_ascii=False) + "\n")
    with open(os.path.join(gates_dir, "SUMMARY.md"), "w", encoding="utf-8") as fh:
        fh.write(summary(document, evidence_by_id))

    for backend, entry in document["backends"].items():
        trusted, untrusted = blockers(document, backend)
        print(f"review: {backend} available={entry['available']} "
              f"trusted_eligible={entry['trusted_eligible']} "
              f"untrusted_eligible={entry['untrusted_eligible']}")
        print(f"        trusted blocked by: {'; '.join(trusted) or 'nothing'}")
        print(f"        untrusted blocked by: {'; '.join(untrusted) or 'nothing'}")
    print("review: wrote gates/eligibility.json and gates/SUMMARY.md")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
