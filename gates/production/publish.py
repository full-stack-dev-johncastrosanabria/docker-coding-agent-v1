"""Compose gates/PRODUCTION-CONFORMANCE.json from the T062 runs (tasks.md T062).

The evidence is COMPOSED, never authored. Every criterion's result is derived from named checks in
`gates/production/work/foundation.json` (the sandbox foundation, per backend x trust profile) and
`gates/production/work/behavior-<backend>.json` (the behaviour half). A criterion whose checks are
absent is `NOT-RUN`, never PASS: an evidence document that could record a pass for a check that
never ran would make the whole gate worthless.

The file is written as `gates/PRODUCTION-CONFORMANCE.json` because that is the name
`gates/eligibility_rules.load_evidence` reads and the `gate` enum value the evidence schema
requires. tasks.md T062 spells the same document in lower case.

Usage:
  python3 gates/production/publish.py [--codex-not-run "<reason>"]
"""

import argparse
import datetime
import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "gates"))

import eligibility_rules as rules  # noqa: E402

WORK = ROOT / "gates" / "production" / "work"
EVIDENCE = ROOT / "gates" / "PRODUCTION-CONFORMANCE.json"
BACKENDS = ("claude", "codex")

#: criterion id -> (description, foundation check-id prefixes, behaviour check ids, backends)
#: A prefix ending in `.` matches every foundation check in that family.
CRITERIA = (
    ("PC.1", "T062.1 - the effective network restrictions, the pinned base and the global "
             "network-policy fingerprint hold with the FINAL production assets, on every "
             "available trust profile",
     ("base.", "network."), (), BACKENDS),
    ("PC.2", "T062.2 - no SSH agent reaches the sandbox: no socket, no SSH_AGENT_PID, and "
             "ssh-add cannot connect",
     ("ssh.isolation",), (), BACKENDS),
    ("PC.3", "T062.3 - shared skills are off and the skill root holds EXACTLY the four runtime "
             "skills; the kit manifest validates in the VM and every skill file matches its "
             "entry; tampering with a skill file, adding an unlisted one, or breaking the gate "
             "makes the preflight refuse the run and the gate deny skill loads (class 26)",
     ("skills.exact_four", "kit.manifest_present", "kit.preflight"),
     ("failclosed.healthy_baseline", "failclosed.preflight_blocks",
      "failclosed.tampered_skill_is_denied", "failclosed.restores_cleanly",
      "skill.trusted_copy_matches_manifest"), BACKENDS),
    ("PC.4", "T062.4 (Claude) - a hostile repository cannot widen the managed permission rules, "
             "add a hook or override the pinned environment; the production dca-gate runs under "
             "/usr/bin/python3 -I, loads shellparse only from /opt/dca/lib, denies the marker "
             "action, and exits 2 when its module or interpreter is missing",
     (), ("gate.decisions", "gate.trusted_module_only", "hostile.no_repository_hook_or_env",
          "hostile.managed_layer_is_trusted_root", "failclosed.gate_denies_when_broken"),
     ("claude",)),
    ("PC.5", "T062.5 (Claude) - the managed agents and skills load and are not shadowed: the "
             "verification skill Claude actually loaded is byte-identical to the kit-installed "
             "trusted copy, no hostile body was loaded, the non-allowlisted repository skill is "
             "denied (class 26), and both managed subagents really ran",
     (), ("claude.loaded_skill_is_trusted", "run.delegation_researcher",
          "run.delegation_reviewer", "run.skill_load_allowed", "stream.no_hostile_marker",
          "run.positive_control"), ("claude",)),
    ("PC.6", "T062.6 (Codex) - the production policy pipeline under --safety strict: allowed "
             "operations execute with the approval recorded by the pre_tool_use hook, real "
             "root->researcher and root->reviewer delegations happen, a class-2 sensitive read is "
             "denied with DCA_DENY, a natively denied action stays denied under a gate stub that "
             "would allow it, an in-VM user config that sets safety/yolo/permissions makes the "
             "preflight refuse, and a broken gate blocks the tool call",
     (), ("gate.decisions", "gate.trusted_module_only", "run.positive_control",
          "run.hook_mediated_every_call", "run.delegation_researcher", "run.delegation_reviewer",
          "run.skill_load_allowed", "codex.native_deny_holds_under_stub",
          "codex.real_gate_restored", "failclosed.user_config_cannot_weaken_safety",
          "failclosed.gate_denies_when_broken"), ("codex",)),
    ("PC.7", "T062.7 (Codex) - the effective capabilities inside the VM: root has the filesystem, "
             "shell, delegation and skill tools and can delegate to both subagents; the "
             "researcher is read-only; the reviewer has the three git review tools and no shell "
             "or write tool; a reviewer mutation is denied (class 27) and the candidate's "
             "workspace fingerprint stays identical",
     (), ("codex.effective_tools", "codex.root_can_delegate", "codex.researcher_read_only",
          "codex.reviewer_review_tools", "run.read_only_roles_never_mutated",
          "run.reviewer_left_workspace_identical", "run.reviewer_probe_absent"), ("codex",)),
    ("PC.8", "T062.8 (Codex) - skill-source integrity against a hostile repository that ships "
             "same-named verification skills at every root and nested scan position: debug "
             "skills lists exactly the four runtime skills from <KIT_DIR>/skills at both working "
             "directories, the loaded bytes are the trusted ones, no hostile marker appears in "
             "the stream, and the non-allowlisted repository skill is unavailable",
     (), ("codex.skill_set_exact", "codex.skill_paths_under_kit",
          "codex.no_hostile_skill_listed", "skill.loaded_bytes_are_trusted",
          "stream.no_hostile_marker"), ("codex",)),
)


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8")) if Path(path).is_file() else None


def foundation_checks(document, backend):
    """{check-id: result} merged across every trust profile of one backend."""
    merged = {}
    if not document:
        return merged
    for cell in document.get("cells", []):
        if cell.get("backend") != backend:
            continue
        for row in cell.get("checks", []):
            key = f"{cell['profile']}:{row['id']}"
            merged[key] = row["result"]
    return merged


def behaviour_checks(document):
    return {row["id"]: row["result"] for row in (document or {}).get("checks", [])}


def resolve(criterion, foundation, behaviour, backend):
    identifier, description, prefixes, behaviour_ids, backends = criterion
    if backend not in backends:
        return {"id": identifier, "description": description, "result": "NOT-APPLICABLE",
                "evidence_ref": f"not a {backend} requirement"}
    matched = {key: value for key, value in foundation.items()
               if any(key.split(":", 1)[1].startswith(prefix) for prefix in prefixes)}
    missing = [name for name in behaviour_ids if name not in behaviour]
    if (prefixes and not matched) or missing:
        return {"id": identifier, "description": description, "result": "NOT-RUN",
                "evidence_ref": ("missing: " + ", ".join(missing[:6])) if missing
                else "no foundation check matched"}
    failures = sorted([key for key, value in matched.items() if value != "PASS"]
                      + [name for name in behaviour_ids if behaviour[name] != "PASS"])
    return {
        "id": identifier, "description": description,
        "result": "PASS" if not failures else "FAIL",
        "evidence_ref": (f"{len(matched)} foundation + {len(behaviour_ids)} behaviour checks PASS"
                         if not failures else "FAILED: " + ", ".join(failures[:8])),
    }


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--codex-not-run", default=None,
                        help="record codex NOT-RUN with this reason instead of reading its "
                             "behaviour evidence")
    options = parser.parse_args(argv)

    versions = read(ROOT / "runtime" / "versions.yaml")
    eligibility = read(ROOT / "gates" / "eligibility.json")
    foundation = read(WORK / "foundation.json")
    if foundation is None:
        raise SystemExit("gates/production/work/foundation.json is missing: run "
                         "gates/production/run.py first")

    backends = {}
    for backend in BACKENDS:
        available = ((eligibility or {}).get("backends", {}).get(backend, {}).get("available"))
        if not available:
            backends[backend] = {
                "status": "NOT-APPLICABLE",
                "not_run_reason": f"{backend} is not available per gates/eligibility.json",
            }
            continue
        if backend == "codex" and options.codex_not_run:
            backends[backend] = {"status": "NOT-RUN", "not_run_reason": options.codex_not_run}
            continue
        behaviour_doc = read(WORK / f"behavior-{backend}.json")
        found = foundation_checks(foundation, backend)
        behaviour = behaviour_checks(behaviour_doc)
        criteria = [resolve(item, found, behaviour, backend) for item in CRITERIA]
        applicable = [row for row in criteria if row["result"] != "NOT-APPLICABLE"]
        if any(row["result"] == "FAIL" for row in applicable):
            status = "FAIL"
        elif any(row["result"] == "NOT-RUN" for row in applicable):
            status = "NOT-RUN"
        else:
            status = "PASS"
        entry = {"status": status, "criteria": criteria}
        if status == "NOT-RUN":
            entry["not_run_reason"] = "; ".join(
                f"{row['id']}: {row['evidence_ref']}" for row in applicable
                if row["result"] == "NOT-RUN")[:400]
        backends[backend] = entry

    statuses = {name: entry["status"] for name, entry in backends.items()}
    applicable = [value for value in statuses.values() if value != "NOT-APPLICABLE"]
    # PARTIAL is reserved for G11 by the accepted evidence schema, so a gate that is complete for
    # one backend and unrun for another records NOT-RUN with its reason. It costs nothing: the
    # review reads production conformance PER BACKEND, so a backend that is PASS here is eligible
    # whatever the other backend's status is.
    overall = ("FAIL" if "FAIL" in applicable
               else "PASS" if applicable and set(applicable) == {"PASS"}
               else "NOT-RUN")

    document = {
        "gate": "PRODUCTION-CONFORMANCE",
        "status": overall,
        "backends": backends,
        "run_at": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "versions": {"sbx": (versions.get("sbx") or {}).get("exact"),
                     "docker_agent": versions.get("docker_agent"),
                     "docker_agent_config_version": versions.get("docker_agent_config_version")},
        "provenance": {"runtime_versions_digest": rules.runtime_versions_digest(versions)},
        "network_policy_fingerprint": foundation.get("global_policy_fingerprint"),
        "criteria": [
            {"id": "PC.0a",
             "description": "the live global network-policy fingerprint still equals the one the "
                            "accepted G4 evidence recorded, so conformance was observed under the "
                            "policy G4 actually verified",
             "result": ("PASS" if foundation.get("global_policy_fingerprint")
                        == (eligibility or {}).get("network_policy_fingerprint") else "FAIL"),
             "evidence_ref": foundation.get("global_policy_fingerprint")},
            {"id": "PC.0b",
             "description": "every applicable backend x trust-profile foundation cell passed and "
                            "left zero sandboxes behind",
             "result": ("PASS" if foundation.get("cells") and
                        all(cell.get("status") == "PASS" for cell in foundation["cells"])
                        else "FAIL"),
             "evidence_ref": ", ".join(f"{cell['backend']}/{cell['profile']}={cell['status']}"
                                       for cell in foundation.get("cells", []))},
        ],
        "not_run_reason": ("; ".join(f"{name}: {entry['status']}"
                                     for name, entry in sorted(backends.items())
                                     if entry["status"] not in ("PASS", "NOT-APPLICABLE"))
                           or None) if overall == "NOT-RUN" else None,
        "notes": ("Composed by gates/production/publish.py from "
                  "gates/production/work/foundation.json and behavior-<backend>.json. Per-backend "
                  "by contract: a backend that is NOT-RUN or FAIL here never blocks another "
                  "backend's trusted eligibility."),
    }
    EVIDENCE.write_text(json.dumps(document, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"status": overall, "backends": statuses, "path": str(EVIDENCE)}, indent=2))
    # Non-zero only when something actually FAILED. A backend that has simply not been run yet
    # is a complete, accurate record, not an error in composing it.
    return 1 if any(value == "FAIL" for value in statuses.values()) else 0


if __name__ == "__main__":
    sys.exit(main())
