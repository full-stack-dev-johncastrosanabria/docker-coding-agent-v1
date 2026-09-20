"""Evaluate the G5 observations and write gates/G5.json (tasks.md T013).

Standard library only, and nothing global changes. G5 proves the return half of R11: a task
commit on dca/<run-id> leaves the VM as a bundle, reaches a host quarantine directory by
sbx cp, is verified and identity-checked there, and only then is fetched as exactly that one
ref — with the host HEAD, attachment, index, working tree and every other ref byte-identical,
no checkout or merge, and the VM removed. Two negatives must each end with no ref and no host
change: a corrupted returned bundle, and a retrieval copy failure.

Fail-closed: the import may only run after the returned bundle verified and advertised exactly
one head matching the expected commit and ref, and any missing, malformed or unexpected
observation fails its criterion rather than being read as success.

Usage:
  python3 gates/G5/record.py <observations.env> [<work-dir>]   write gates/G5.json
  python3 gates/G5/record.py --preflight <obs> [<work-dir>]    post-G0 state, zero sandboxes
  python3 gates/G5/record.py --returned-ok <obs> [<work-dir>]  exit 0 only if the returned
                                                               bundle may be imported
  python3 gates/G5/record.py --negative-a-ok <obs> [<work-dir>]  exit 0 only if the corrupted
                                                                 bundle verified (it must not)
  python3 gates/G5/record.py --negative-b-ok <obs> [<work-dir>]  exit 0 only if the retrieval
                                                                 copy succeeded (it must not)
"""

import datetime
import importlib.util
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
WORK = os.path.join(ROOT, "gates", "G5", "work")
VERSIONS = os.path.join(ROOT, "runtime", "versions.yaml")
EVIDENCE = os.path.join(ROOT, "gates", "G5.json")

SANDBOX = "dca-g5-roundtrip"
RUN_ID = "g5-happy"
NEG_A = "g5-corrupt"
NEG_B = "g5-copyfail"
TASK_REF = f"refs/heads/dca/{RUN_ID}"
PREFLIGHT = ("G5.0a", "G5.0b", "G5.0c", "G5.0d")
SNAPSHOT_PARTS = ("head", "symref", "refs", "status", "index", "files")


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


preflight_module = _load("dca_preflight", os.path.join(ROOT, "gates", "preflight.py"))
rules_module = _load("eligibility_rules", os.path.join(ROOT, "gates", "eligibility_rules.py"))


def read_observations(path):
    observations = {}
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            key, sep, value = line.rstrip("\n").partition("=")
            if sep:
                observations[key] = value
    return observations


def read_json(directory, name):
    try:
        with open(os.path.join(directory, name), encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return None


def read_text(work, name):
    try:
        with open(os.path.join(work, name), encoding="utf-8") as fh:
            return fh.read()
    except OSError:
        return None


def count(obs, key):
    value = obs.get(key)
    return int(value) if (value or "").isdigit() else None


def sandbox_names(listing):
    entries = listing.get("sandboxes") if isinstance(listing, dict) else None
    if not isinstance(entries, list):
        return None
    return sorted(e.get("name") for e in entries if isinstance(e, dict))


def snapshot(work, label):
    """A host snapshot as a dict of part -> text, or None when any part is missing."""
    out = {}
    for part in SNAPSHOT_PARTS:
        text = read_text(work, f"snap-{label}-{part}.txt")
        if text is None:
            return None
        out[part] = text
    return out


def refs_map(snapshot_text):
    """{refname: objectid} from a for-each-ref capture."""
    out = {}
    for line in (snapshot_text or "").splitlines():
        parts = line.split()
        if len(parts) == 2:
            out[parts[1]] = parts[0]
    return out


def host_unchanged_failures(work, baseline_label, other_label, expected_new_refs=()):
    """Why the host changed beyond the expected new refs, as reasons (empty = it didn't)."""
    baseline = snapshot(work, baseline_label)
    other = snapshot(work, other_label)
    if baseline is None or other is None:
        return [f"a host snapshot was not captured ({baseline_label}/{other_label})"]
    reasons = []
    for part in ("head", "symref", "status", "index", "files"):
        if baseline[part] != other[part]:
            reasons.append(f"the host {part} changed between {baseline_label} and {other_label}")
    before = refs_map(baseline["refs"])
    after = refs_map(other["refs"])
    for name, oid in before.items():
        if name not in after:
            reasons.append(f"ref {name} disappeared")
        elif after[name] != oid:
            reasons.append(f"ref {name} moved from {oid} to {after[name]}")
    unexpected = sorted(set(after) - set(before) - set(expected_new_refs))
    if unexpected:
        reasons.append(f"unexpected new refs: {unexpected}")
    missing = [ref for ref in expected_new_refs if ref not in after]
    if missing:
        reasons.append(f"expected new refs absent: {missing}")
    return reasons


def advertised_head(obs, key="returned_head_line"):
    """(sha, ref) from a single advertised head line, or None when it isn't exactly that."""
    fields = (obs.get(key) or "").strip().split()
    if len(fields) != 2:
        return None
    sha, ref = fields
    if len(sha) != 40 or any(c not in "0123456789abcdef" for c in sha.lower()):
        return None
    return sha, ref


def returned_bundle_failures(obs):
    """Why the returned bundle may not be imported, as reasons (empty = it may)."""
    reasons = []
    if obs.get("cp_out_exit") != "0":
        reasons.append(f"sbx cp out of the VM failed (exit {obs.get('cp_out_exit', '-')})")
    if obs.get("quarantine_inside_git") != "no":
        reasons.append("the quarantine directory is inside the host repository's .git")
    if not count(obs, "quarantine_bundle_bytes"):
        reasons.append("the quarantine bundle is empty or its size was not reported")
    if obs.get("returned_verify_exit") != "0":
        reasons.append(f"git bundle verify failed (exit {obs.get('returned_verify_exit', '-')})")
    # Header verification is not enough: this git version accepts a truncated bundle, so the
    # objects must also unpack cleanly into a throwaway quarantine repository.
    if obs.get("returned_scratch_fetch_exit") != "0":
        reasons.append(
            f"the bundle did not unpack into the scratch quarantine repository "
            f"(exit {obs.get('returned_scratch_fetch_exit', '-')})"
        )
    if obs.get("returned_scratch_ref") != obs.get("vm_candidate_commit"):
        reasons.append(
            f"the scratch quarantine ref is {obs.get('returned_scratch_ref') or 'absent'!r}, not the "
            f"candidate commit {obs.get('vm_candidate_commit')!r}"
        )
    if obs.get("returned_heads_exit") != "0":
        reasons.append(f"git bundle list-heads failed (exit {obs.get('returned_heads_exit', '-')})")
    if count(obs, "returned_head_count") != 1:
        reasons.append(
            f"the returned bundle advertises {obs.get('returned_head_count', 'an unreported number of')} heads, not exactly one"
        )
    head = advertised_head(obs)
    candidate = obs.get("vm_candidate_commit") or ""
    if head is None:
        reasons.append(f"the advertised head {obs.get('returned_head_line')!r} is not one SHA and one ref")
    else:
        sha, ref = head
        if not candidate or sha != candidate:
            reasons.append(f"the advertised SHA {sha!r} is not the candidate commit {candidate!r}")
        if ref != TASK_REF:
            reasons.append(f"the advertised ref {ref!r} is not exactly {TASK_REF}")
    return reasons


def vm_failures(obs):
    """Why the in-VM task commit isn't the expected one."""
    reasons = []
    if obs.get("vm_probe_complete") != "yes" or obs.get("vm_exit") != "0":
        return ["the in-VM step did not complete"]
    if obs.get("vm_clone_exit") != "0":
        reasons.append("the in-VM clone failed")
    if obs.get("vm_hooks_path") != "/dev/null":
        reasons.append(f"core.hooksPath is {obs.get('vm_hooks_path')!r}, not /dev/null")
    if obs.get("vm_task_branch_exit") != "0":
        reasons.append("the task branch was not created")
    if obs.get("vm_task_branch_base") != obs.get("source_commit"):
        reasons.append(
            f"the task branch started at {obs.get('vm_task_branch_base')!r}, not source.commit "
            f"{obs.get('source_commit')!r}"
        )
    if obs.get("vm_candidate_ref") != TASK_REF:
        reasons.append(f"the candidate ref is {obs.get('vm_candidate_ref')!r}, not {TASK_REF}")
    candidate = obs.get("vm_candidate_commit") or ""
    if len(candidate) != 40:
        reasons.append("the candidate commit was not reported")
    elif candidate == obs.get("source_commit"):
        reasons.append("the candidate commit equals source.commit, so nothing was committed")
    if obs.get("vm_task_bundle_exit") != "0":
        reasons.append("git bundle create failed in the VM")
    if count(obs, "vm_task_bundle_heads") != 1:
        reasons.append("the exported bundle does not advertise exactly one head")
    return reasons


def import_failures(obs, work):
    """Why the import isn't exactly one new ref at the candidate commit."""
    reasons = []
    if obs.get("candidate_ref_exists_before") != "no":
        reasons.append(f"{TASK_REF} already existed before the import")
    if obs.get("fetch_attempted") != "yes":
        reasons.append("the import never ran, so the round trip is unproven")
    elif obs.get("fetch_exit") != "0":
        reasons.append(f"git fetch failed (exit {obs.get('fetch_exit', '-')})")
    if obs.get("candidate_ref_after") != obs.get("vm_candidate_commit"):
        reasons.append(
            f"{TASK_REF} is {obs.get('candidate_ref_after') or 'absent'!r} after the import, not the "
            f"candidate commit {obs.get('vm_candidate_commit')!r}"
        )
    reasons += host_unchanged_failures(work, "baseline", "post", expected_new_refs=(TASK_REF,))
    return reasons


def negative_failures(obs, work, run_id, label, snapshot_label):
    """Why a negative path isn't clean: it must import nothing and change nothing."""
    reasons = []
    if obs.get(f"{run_id}_fetch_attempted") != "no":
        reasons.append(f"{label}: the import ran despite the failure")
    if obs.get(f"{run_id}_ref_after"):
        reasons.append(f"{label}: refs/heads/dca/{run_id} exists at {obs.get(f'{run_id}_ref_after')}")
    reasons += [
        f"{label}: {reason}"
        for reason in host_unchanged_failures(work, "post", snapshot_label, expected_new_refs=())
    ]
    return reasons


def preflight(obs, work):
    with open(VERSIONS, encoding="utf-8") as fh:
        pins = json.load(fh)
    version = read_json(work, "pf-version.json")
    forwarding = read_json(work, "pf-ssh-forwarding.json")
    socket_path = read_json(work, "pf-ssh-socket.json")
    policy = read_json(work, "pf-policy.json")
    refusals = preflight_module.ssh_forwarding_refusals(forwarding, socket_path)
    baseline = sandbox_names(read_json(work, "ls-baseline.json"))

    return [
        (
            "G5.0a",
            "sbx client and server still match the pinned exact version and the server is running",
            obs.get("pf_version_exit") == "0"
            and preflight_module.version_pins_hold(version, pins["sbx"]["exact"]),
            f"pin {pins['sbx']['exact']}, client {(version or {}).get('client', {}).get('version')}, "
            f"server {(version or {}).get('server', {}).get('version')} state "
            f"{(version or {}).get('server', {}).get('state')}",
        ),
        (
            "G5.0b",
            "the accepted SSH baseline is unchanged and sbx calls drop the host agent socket",
            obs.get("pf_ssh_forwarding_exit") == "0"
            and obs.get("pf_ssh_socket_exit") == "0"
            and refusals == []
            and obs.get("sbx_env_ssh_auth_sock") == "removed",
            f"detector refusals {refusals or 'none'}, host SSH_AUTH_SOCK as seen by sbx "
            f"{obs.get('sbx_env_ssh_auth_sock', '-')}",
        ),
        (
            "G5.0c",
            "global network policy is still exactly the post-G0 bootstrap baseline",
            obs.get("pf_policy_exit") == "0"
            and policy is not None
            and preflight_module.network_baseline_holds(policy),
            f"{len(preflight_module.network_rules(policy))} network rule(s) "
            f"{[r.get('id') for r in preflight_module.network_rules(policy)]}",
        ),
        (
            "G5.0d",
            "the run starts from zero sandboxes",
            obs.get("pf_ls_exit") == "0" and baseline == [],
            f"baseline listing: {baseline if baseline is not None else 'unreadable'}",
        ),
    ]


def evaluate(obs, work):
    rows = [(i, d, r, o, True) for i, d, r, o in preflight(obs, work)]
    ready = all(row[2] for row in rows)

    with open(VERSIONS, encoding="utf-8") as fh:
        pins = json.load(fh)
    image = obs.get(f"image_{SANDBOX}") or ""
    pinned_base = pins["sandbox_bases"]["claude"]["base"]
    pinned_digest = pins["sandbox_bases"]["claude"]["version"]
    repository, _, tag = (pinned_base or "").rpartition(":")
    cached = preflight_module.template_image(read_json(work, "templates.json"), repository, tag)
    vm_done = obs.get("vm_exit") == "0"

    rows.append(
        (
            "G5.1",
            "mountless sandbox with --skills=off from the exact pinned Claude base, input bundle copied in",
            obs.get(f"create_{SANDBOX}_exit") == "0"
            and obs.get("cp_in_exit") == "0"
            and obs.get("input_bundle_verify_exit") == "0"
            and "no workspace bind mount" in obs.get(f"workspace_line_{SANDBOX}", "")
            and image == pinned_base
            and preflight_module.template_identity_matches(cached, pinned_base, pinned_digest),
            f"create exit {obs.get(f'create_{SANDBOX}_exit', '-')}, input bundle verify "
            f"{obs.get('input_bundle_verify_exit', '-')}, sbx cp in {obs.get('cp_in_exit', '-')}, "
            f"base {image or 'not reported'} (pin {pinned_base}) with cached template id "
            f"{(cached or {}).get('id', '-')}, workspace "
            f"'{obs.get(f'workspace_line_{SANDBOX}', 'not reported')}'",
            ready,
        )
    )
    rows.append(
        (
            "G5.2",
            f"the task branch is created at source.commit, hooks disabled, and one commit is made on {TASK_REF}",
            vm_done and vm_failures(obs) == [],
            f"source.commit {obs.get('source_commit', '-')}, task branch base "
            f"{obs.get('vm_task_branch_base', '-')}, ref {obs.get('vm_candidate_ref', '-')}, "
            f"candidate {obs.get('vm_candidate_commit', '-')}, core.hooksPath "
            f"{obs.get('vm_hooks_path', '-')}, exported heads {obs.get('vm_task_bundle_heads', '-')}; "
            f"failures {vm_failures(obs) or 'none'}",
            ready,
        )
    )
    rows.append(
        (
            "G5.3",
            "the returned bundle is quarantined outside .git, fully validated, and advertises exactly the task ref",
            returned_bundle_failures(obs) == [],
            f"sbx cp out {obs.get('cp_out_exit', '-')}, quarantine inside .git "
            f"{obs.get('quarantine_inside_git', '-')}, {obs.get('quarantine_bundle_bytes', '-')} bytes, "
            f"verify exit {obs.get('returned_verify_exit', '-')}, scratch-repo unpack exit "
            f"{obs.get('returned_scratch_fetch_exit', '-')} at "
            f"{obs.get('returned_scratch_ref', '-')}, advertised heads "
            f"{obs.get('returned_head_count', '-')}: '{obs.get('returned_head_line', '-')}'; "
            f"failures {returned_bundle_failures(obs) or 'none'}",
            ready and vm_done,
        )
    )
    rows.append(
        (
            "G5.4",
            f"the import adds exactly {TASK_REF} at the candidate commit, with no checkout or merge",
            import_failures(obs, work) == [],
            f"{TASK_REF} before: {obs.get('candidate_ref_exists_before', '-')}, fetch attempted "
            f"{obs.get('fetch_attempted', '-')} exit {obs.get('fetch_exit', '-')}, ref after "
            f"{obs.get('candidate_ref_after') or 'absent'}; host HEAD "
            f"{obs.get('snapshot_baseline_head', '-')} -> {obs.get('snapshot_post_head', '-')}, refs "
            f"{obs.get('snapshot_baseline_refs', '-')} -> {obs.get('snapshot_post_refs', '-')}; "
            f"failures {import_failures(obs, work) or 'none'}",
            ready and vm_done,
        )
    )
    rows.append(
        (
            "G5.n1",
            "negative: a corrupted returned bundle fails validation, imports nothing and changes nothing",
            # Validation as a whole must reject it. On this git version `git bundle verify`
            # alone accepts a truncated bundle, which is why validation also unpacks it.
            (
                obs.get(f"{NEG_A}_verify_exit") not in (None, "0")
                or obs.get(f"{NEG_A}_scratch_fetch_exit") not in (None, "0")
            )
            and not obs.get(f"{NEG_A}_scratch_ref")
            and negative_failures(obs, work, NEG_A, "corrupted bundle", "after-negative-a") == [],
            f"copied {obs.get(f'{NEG_A}_bytes_before', '-')} bytes, truncated to "
            f"{obs.get(f'{NEG_A}_bytes_after', '-')}; git bundle verify exit "
            f"{obs.get(f'{NEG_A}_verify_exit', '-')} (header check alone), scratch-repo unpack exit "
            f"{obs.get(f'{NEG_A}_scratch_fetch_exit', '-')} (non-zero required), scratch ref "
            f"{obs.get(f'{NEG_A}_scratch_ref') or 'absent'}, import attempted "
            f"{obs.get(f'{NEG_A}_fetch_attempted', '-')}, refs/heads/dca/{NEG_A} "
            f"{obs.get(f'{NEG_A}_ref_after') or 'absent'}; failures "
            f"{negative_failures(obs, work, NEG_A, 'corrupted bundle', 'after-negative-a') or 'none'}",
            ready and vm_done,
        )
    )
    rows.append(
        (
            "G5.n2",
            "negative: a failed retrieval copy imports nothing, quarantines nothing and changes nothing",
            obs.get(f"cp_out_{NEG_B}_exit") not in (None, "0")
            and obs.get(f"{NEG_B}_vm_bundle") == "present"
            and obs.get(f"{NEG_B}_quarantine_exists") == "no"
            and negative_failures(obs, work, NEG_B, "copy failure", "after-negative-b") == [],
            f"the VM's own bundle is {obs.get(f'{NEG_B}_vm_bundle', '-')}, retrieval sbx cp exit "
            f"{obs.get(f'cp_out_{NEG_B}_exit', '-')} (non-zero required), quarantine file "
            f"{obs.get(f'{NEG_B}_quarantine_exists', '-')}, import attempted "
            f"{obs.get(f'{NEG_B}_fetch_attempted', '-')}, refs/heads/dca/{NEG_B} "
            f"{obs.get(f'{NEG_B}_ref_after') or 'absent'}; failures "
            f"{negative_failures(obs, work, NEG_B, 'copy failure', 'after-negative-b') or 'none'}",
            ready and vm_done,
        )
    )

    left = sandbox_names(read_json(work, "ls-after.json"))
    rows.append(
        (
            "G5.c1",
            "the VM is disposed of: the named sandbox is removed and none remains",
            (obs.get(f"rm_{SANDBOX}_exit") == "0" or obs.get(f"create_{SANDBOX}_exit") is None)
            and left == [],
            f"sbx rm --force {SANDBOX} exit {obs.get(f'rm_{SANDBOX}_exit', '-')} "
            f"(sandbox created: {'yes' if obs.get(f'create_{SANDBOX}_exit') is not None else 'no'}), "
            f"listing after cleanup {left if left is not None else 'unreadable'}",
            f"rm_{SANDBOX}_exit" in obs,
        )
    )

    criteria = []
    for identifier, description, result, observed, was_observed in rows:
        if was_observed:
            outcome = "PASS" if result else "FAIL"
        else:
            outcome = "NOT-RUN"
            observed = "this step did not run, because an earlier one did not hold (fail-closed)"
        criteria.append(
            {"id": identifier, "description": description, "result": outcome, "evidence_ref": observed}
        )
    return criteria


def record(obs_path, work=WORK, versions_path=VERSIONS, evidence_path=EVIDENCE):
    obs = read_observations(obs_path)
    criteria = evaluate(obs, work)
    status = "PASS" if all(row["result"] == "PASS" for row in criteria) else "FAIL"
    with open(versions_path, encoding="utf-8") as fh:
        versions = json.load(fh)
    evidence = {
        "gate": "G5",
        "status": status,
        "run_at": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "versions": {
            "sbx": (read_json(work, "pf-version.json") or {}).get("client", {}).get("version") or None
        },
        "provenance": rules_module.evidence_provenance("G5", versions),
        "criteria": criteria,
        "fallback_applied": None,
        "notes": (
            "The return half of R11, proven gate-locally against a fixture repository, never "
            "this project's repository. A task commit on " + TASK_REF + " is bundled in the VM, "
            "copied out with sbx cp into a quarantine directory outside .git, verified there, "
            "and imported only after the single advertised head matches the candidate commit and "
            "the task ref exactly. Validation is not `git bundle verify` alone: that checks the "
            "header and prerequisites and, on the git version observed here, accepts a truncated "
            "bundle, so the objects must also unpack cleanly into a throwaway quarantine "
            "repository before the host is touched. The host is snapshotted before and after: HEAD, attachment, "
            "every ref and object id, porcelain status, the index hash and the bytes of every "
            "tracked file, so a checkout, merge or moved ref would show. Two negatives run "
            "separately and must each import nothing and change nothing: a truncated returned "
            "bundle, and a retrieval copy that fails while the VM's own bundle is intact. "
            "T070/T071 own production retrieval and finalization; this is not launcher code. "
            "The sandbox is removed by name; sbx rm --all and sbx reset are never used."
        ),
    }
    with open(evidence_path, "w", encoding="utf-8") as fh:
        fh.write(json.dumps(evidence, indent=2, ensure_ascii=False) + "\n")
    return status, criteria


def main(argv):
    args = argv[1:]
    mode = args.pop(0) if args and args[0].startswith("--") else None
    path = args[0] if args else None
    work = args[1] if len(args) > 1 else WORK
    modes = (None, "--preflight", "--returned-ok", "--negative-a-ok", "--negative-b-ok")
    if path is None or mode not in modes:
        print("usage: python3 gates/G5/record.py [--preflight|--returned-ok|--negative-a-ok|"
              "--negative-b-ok] <observations.env> [<work-dir>]", file=sys.stderr)
        return 2
    obs = read_observations(path)
    if mode == "--preflight":
        rows = preflight(obs, work)
        for identifier, description, result, observed in rows:
            print(f"  {identifier} {'PASS' if result else 'FAIL'} {description}")
            print(f"          observed: {observed}")
        return 0 if all(result for _, _, result, _ in rows) else 1
    if mode == "--returned-ok":
        return 0 if returned_bundle_failures(obs) == [] else 1
    if mode == "--negative-a-ok":
        # Exits 0 only if the corrupted bundle passed the whole validation, which must never
        # happen: header verification plus a clean unpack into the scratch quarantine repo.
        return 0 if (
            obs.get(f"{NEG_A}_verify_exit") == "0"
            and obs.get(f"{NEG_A}_scratch_fetch_exit") == "0"
        ) else 1
    if mode == "--negative-b-ok":
        return 0 if obs.get(f"cp_out_{NEG_B}_exit") == "0" else 1

    status, criteria = record(path, work)
    print(f"G5: {status} ({EVIDENCE})")
    for row in criteria:
        print(f"  {row['id']:<8} {row['result']:<8} {row['description']}")
        print(f"          observed: {row['evidence_ref']}")
    return 0 if status == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
