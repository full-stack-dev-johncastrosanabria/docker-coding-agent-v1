"""Evaluate the G10 observations and write gates/G10.json (tasks.md T012).

Standard library only, and nothing global changes. G10 proves two independent properties:

  Phase A  the dirty-tree preflight fails closed with no override: an uncommitted tracked edit
           or an untracked non-ignored file refuses the run before any bundle or sandbox
           exists, and ignored files alone never trigger it.
  Phase B  under the explicit gate-local --ignore-uncommitted equivalent, only the selected
           committed branch state reaches a mountless sandbox: no host workspace mount, none of
           the dirty canaries, and nothing from the second branch.

Neither is allowed to substitute for the other, so each has its own criteria and the recorder
fails closed on missing or malformed evidence for either.

Canary values never reach this file: the observations carry IDs and match counts only.

Usage:
  python3 gates/G10/record.py <observations.env> [<work-dir>]   write gates/G10.json
  python3 gates/G10/record.py --preflight <obs> [<work-dir>]    post-G0 state and zero sandboxes
  python3 gates/G10/record.py --phase-a <obs> [<work-dir>]      exit 0 only if Phase A holds
"""

import datetime
import importlib.util
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
WORK = os.path.join(ROOT, "gates", "G10", "work")
VERSIONS = os.path.join(ROOT, "runtime", "versions.yaml")
EVIDENCE = os.path.join(ROOT, "gates", "G10.json")

SANDBOX = "dca-g10-source"
SELECTED_BRANCH = "dca-g10-selected"
SELECTED_REF = f"refs/heads/{SELECTED_BRANCH}"
SECOND_BRANCH = "dca-g10-second"
PREFLIGHT = ("G10.0a", "G10.0b", "G10.0c", "G10.0d")
PHASE_A = ("G10.a1", "G10.a2", "G10.a3")
# The decision matrix gates/G10/dirty_tree.sh must produce: refuse with exit 3, allow with 0.
REFUSE_EXIT = "3"
ALLOW_EXIT = "0"
DECISION_MATRIX = {
    "tracked_only": ("refuse", REFUSE_EXIT, 1, 0),
    "untracked_only": ("refuse", REFUSE_EXIT, 0, 1),
    "both": ("refuse", REFUSE_EXIT, 1, 1),
    "ignored_only": ("allow", ALLOW_EXIT, 0, 0),
    "clean": ("allow", ALLOW_EXIT, 0, 0),
}
# Recorded verbatim by run.sh; Phase B is only valid when the override was explicitly exercised.
OVERRIDE_MARKER = "gate-local --ignore-uncommitted equivalent, explicitly exercised"


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


def read_env(work, name):
    try:
        with open(os.path.join(work, name), encoding="utf-8") as fh:
            text = fh.read()
    except OSError:
        return {}
    out = {}
    for line in text.splitlines():
        key, sep, value = line.partition("=")
        if sep:
            out[key.strip()] = value.strip()
    return out


def count(obs, key):
    """An observation that must be a non-negative integer, or None (fail closed)."""
    value = obs.get(key)
    return int(value) if (value or "").isdigit() else None


def sandbox_names(listing):
    entries = listing.get("sandboxes") if isinstance(listing, dict) else None
    if not isinstance(entries, list):
        return None
    return sorted(e.get("name") for e in entries if isinstance(e, dict))


def decision_failures(obs):
    """Why the dirty-tree decision matrix isn't proven, as reasons (empty = it is).

    Each case must produce the expected decision *and* its exit code: refuse is exit 3, allow
    is exit 0. An unreported or unexpected value fails; nothing is ever read as ALLOW by
    default.
    """
    reasons = []
    for case, (decision, exit_code, tracked, untracked) in DECISION_MATRIX.items():
        observed_exit = obs.get(f"decide_{case}_exit")
        observed_decision = obs.get(f"decide_{case}_decision")
        if observed_exit != exit_code:
            reasons.append(f"{case}: exit {observed_exit or 'not reported'}, expected {exit_code}")
        if observed_decision != decision:
            reasons.append(f"{case}: decision {observed_decision or 'not reported'!r}, expected {decision}")
        for label, expected in (("tracked", tracked), ("untracked", untracked)):
            observed = count(obs, f"decide_{case}_{label}")
            if observed is None:
                reasons.append(f"{case}: the {label} count was not reported as a number")
            elif (observed >= 1) != (expected >= 1):
                reasons.append(f"{case}: {label} count {observed}, expected {'at least one' if expected else 'none'}")
    return reasons


def ignored_only_failures(obs):
    """Why the ignored-only control isn't proven, as reasons (empty = it is)."""
    reasons = []
    if obs.get("decide_ignored_only_decision") != "allow":
        reasons.append("the ignored-only case did not allow the run")
    if obs.get("decide_ignored_only_exit") != ALLOW_EXIT:
        reasons.append(f"the ignored-only case exited {obs.get('decide_ignored_only_exit', 'not reported')}")
    if obs.get("ignored_only_env_present") != "yes":
        reasons.append("the ignored canary was not present during the control")
    if obs.get("ignored_only_env_is_ignored") != "yes":
        reasons.append("the canary file is not actually ignored by the fixture")
    return reasons


def untouched_failures(obs, work):
    """Why 'nothing was created by a refused path' isn't proven, for every refused case."""
    reasons = []
    baseline = sandbox_names(read_json(work, "ls-baseline.json"))
    if baseline is None:
        return ["the baseline sandbox listing was not readable"]
    if baseline:
        reasons.append(f"the run did not start from zero sandboxes: {baseline}")
    for case, (decision, _, _, _) in DECISION_MATRIX.items():
        if decision != "refuse":
            continue
        if obs.get(f"decide_{case}_bundle_exists") != "no":
            reasons.append(f"{case}: a bundle existed on the refused path")
        if obs.get(f"decide_{case}_ls_exit") != "0":
            reasons.append(f"{case}: the sandbox listing could not be read")
        after = sandbox_names(read_json(work, f"ls-decide-{case}.json"))
        if after is None:
            reasons.append(f"{case}: the sandbox listing after the refusal was not readable")
        elif after != baseline:
            reasons.append(f"{case}: the listing changed across the refusal: {baseline} -> {after}")
    return reasons


def phase_b_precondition_failures(obs):
    """Why Phase B may not proceed: no explicit override, or not the intended dirty tree."""
    reasons = []
    if obs.get("override_ignore_uncommitted") != OVERRIDE_MARKER:
        reasons.append(
            f"the explicit override marker is {obs.get('override_ignore_uncommitted') or 'missing'!r}, "
            f"not {OVERRIDE_MARKER!r}"
        )
    if obs.get("status_before_phase_b_exit") != "0":
        reasons.append("the state Phase B starts from was not observed")
    tracked = count(obs, "phase_b_tracked")
    untracked = count(obs, "phase_b_untracked")
    if tracked is None or untracked is None:
        reasons.append("the Phase B starting counts were not reported as numbers")
        return reasons
    if tracked < 1:
        reasons.append("Phase B does not start from a tracked modification, so the override is untested")
    if untracked < 1:
        reasons.append("Phase B does not start from an untracked file, so the override is untested")
    if obs.get("phase_b_mentions_ignored") != "0":
        reasons.append("the Phase B status mentions the ignored file, so ignored files count as dirty")
    return reasons


def advertised_head(obs):
    """(sha, ref) from the single advertised bundle head, or None when it isn't exactly that."""
    line = (obs.get("bundle_head_line") or "").strip()
    fields = line.split()
    if len(fields) != 2:
        return None
    sha, ref = fields
    if len(sha) != 40 or any(c not in "0123456789abcdef" for c in sha.lower()):
        return None
    return sha, ref


def bundle_failures(obs, work):
    """Why the bundle isn't a verified export of exactly the selected branch ref.

    The advertised head is parsed into exactly two fields and compared for equality, so a
    neighbouring ref such as refs/heads/<selected>-evil can never satisfy it.
    """
    reasons = list(phase_b_precondition_failures(obs))
    if obs.get("selected_ref") != SELECTED_REF:
        reasons.append(f"the selected ref is {obs.get('selected_ref')!r}, not {SELECTED_REF}")
    for key, label in (("bundle_create_exit", "create"), ("bundle_verify_exit", "verify"),
                       ("bundle_heads_exit", "list-heads")):
        if obs.get(key) != "0":
            reasons.append(f"git bundle {label} failed (exit {obs.get(key, '-')})")
    if count(obs, "bundle_head_count") != 1:
        reasons.append(
            f"the bundle advertises {obs.get('bundle_head_count', 'an unreported number of')} heads, not exactly one"
        )
    head = advertised_head(obs)
    commit = obs.get("selected_ref_commit") or ""
    if head is None:
        reasons.append(f"the advertised head {obs.get('bundle_head_line')!r} is not one SHA and one ref")
    else:
        sha, ref = head
        if sha != commit:
            reasons.append(f"the advertised SHA {sha!r} is not the selected commit {commit!r}")
        if ref != SELECTED_REF:
            reasons.append(f"the advertised ref {ref!r} is not exactly {SELECTED_REF}")
    return reasons


def delivery_failures(probe, obs):
    """Why the delivered clone isn't the sanitized selected committed state."""
    reasons = []
    if probe.get("probe_complete") != "yes" or probe.get("clone_exit") != "0":
        return ["the in-VM probe did not complete or the clone failed"]
    if probe.get("checkout_exit") != "0":
        # The bundle carries no HEAD, so the run branch must be created at source.commit (R11).
        return [f"creating the run branch at the selected commit failed (exit {probe.get('checkout_exit', '-')})"]
    if probe.get("source_mount_path_exists") != "no":
        reasons.append("/run/sandbox/source exists in the VM")
    if count(probe, "workspace_mounts") != 0:
        reasons.append(f"host workspace/path mounts: {probe.get('workspace_mounts', 'not reported')}")
    for canary in ("env", "untracked", "dirty"):
        hits = count(probe, f"{canary}_canary_hits")
        if hits is None:
            reasons.append(f"the {canary} canary count was not reported")
        elif hits:
            reasons.append(f"the {canary} canary appears in {hits} file(s) in the VM")
    for path_key, label in (("env_file_present", ".env"), ("untracked_file_present", "the untracked file")):
        if probe.get(path_key) != "no":
            reasons.append(f"{label} is present in the delivered clone")
    baseline = count(probe, "baseline_canary_hits")
    if baseline is None or baseline < 1:
        reasons.append("the committed baseline canary is missing from the delivered clone")
    if probe.get("head_commit") != obs.get("selected_ref_commit"):
        reasons.append(
            f"the delivered head {probe.get('head_commit')!r} is not the selected commit "
            f"{obs.get('selected_ref_commit')!r}"
        )
    if probe.get("selected_commit_present") != "yes":
        reasons.append("the selected commit object is missing from the delivered repository")
    return reasons


def second_branch_failures(probe):
    """Why the second branch isn't proven absent, by identity rather than wording."""
    if probe.get("probe_complete") != "yes":
        return ["the in-VM probe did not complete"]
    reasons = []
    if probe.get("second_commit_present") != "no":
        reasons.append("the second branch's unique commit object is present (git cat-file -e succeeded)")
    if count(probe, "second_ref_hits") != 0:
        reasons.append(f"a second-branch ref is present: {probe.get('second_ref_hits', 'not reported')} hit(s)")
    if count(probe, "second_in_rev_list") != 0:
        reasons.append("the unique commit is reachable from a delivered ref (git rev-list --all)")
    hits = count(probe, "second_canary_hits")
    if hits is None or hits:
        reasons.append(f"the branch-only canary appears in {probe.get('second_canary_hits', 'an unreported number of')} file(s)")
    if probe.get("branch_only_file_present") != "no":
        reasons.append("the branch-only file is present in the delivered clone")
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
            "G10.0a",
            "sbx client and server still match the pinned exact version and the server is running",
            obs.get("pf_version_exit") == "0"
            and preflight_module.version_pins_hold(version, pins["sbx"]["exact"]),
            f"pin {pins['sbx']['exact']}, client {(version or {}).get('client', {}).get('version')}, "
            f"server {(version or {}).get('server', {}).get('version')} state "
            f"{(version or {}).get('server', {}).get('state')}",
        ),
        (
            "G10.0b",
            "the accepted SSH baseline is unchanged and sbx calls drop the host agent socket",
            obs.get("pf_ssh_forwarding_exit") == "0"
            and obs.get("pf_ssh_socket_exit") == "0"
            and refusals == []
            and obs.get("sbx_env_ssh_auth_sock") == "removed",
            f"detector refusals {refusals or 'none'}, host SSH_AUTH_SOCK as seen by sbx "
            f"{obs.get('sbx_env_ssh_auth_sock', '-')}",
        ),
        (
            "G10.0c",
            "global network policy is still exactly the post-G0 bootstrap baseline",
            obs.get("pf_policy_exit") == "0"
            and policy is not None
            and preflight_module.network_baseline_holds(policy),
            f"{len(preflight_module.network_rules(policy))} network rule(s) "
            f"{[r.get('id') for r in preflight_module.network_rules(policy)]}",
        ),
        (
            "G10.0d",
            "the run starts from zero sandboxes, so the Phase A comparison is unambiguous",
            obs.get("pf_ls_exit") == "0" and baseline == [],
            f"baseline listing: {baseline if baseline is not None else 'unreadable'}",
        ),
    ]


def evaluate(obs, work):
    rows = [(i, d, r, o, True) for i, d, r, o in preflight(obs, work)]
    ready = all(row[2] for row in rows)

    with open(VERSIONS, encoding="utf-8") as fh:
        pins = json.load(fh)
    probe = read_env(work, f"probe-{SANDBOX}.env")
    probed = obs.get(f"probe_{SANDBOX}_exit") == "0"
    image = obs.get(f"image_{SANDBOX}") or ""
    pinned_base = pins["sandbox_bases"]["claude"]["base"]
    pinned_digest = pins["sandbox_bases"]["claude"]["version"]
    repository, _, tag = (pinned_base or "").rpartition(":")
    cached = preflight_module.template_image(read_json(work, "templates.json"), repository, tag)

    # --- Phase A -------------------------------------------------------------------------------
    matrix = ", ".join(
        f"{case}: {obs.get(f'decide_{case}_decision', '-')}/exit {obs.get(f'decide_{case}_exit', '-')} "
        f"(tracked {obs.get(f'decide_{case}_tracked', '-')}, untracked {obs.get(f'decide_{case}_untracked', '-')})"
        for case in DECISION_MATRIX
    )
    rows.append(
        (
            "G10.a1",
            "the dirty-tree decision refuses (exit 3) on tracked or untracked dirt and allows (exit 0) otherwise",
            decision_failures(obs) == [],
            f"{matrix}; failures {decision_failures(obs) or 'none'}",
            ready,
        )
    )
    rows.append(
        (
            "G10.a2",
            "every refused case created no bundle and no sandbox, and left the listing unchanged",
            untouched_failures(obs, work) == [],
            "; ".join(
                f"{case}: bundle {obs.get(f'decide_{case}_bundle_exists', '-')}, listing "
                f"{sandbox_names(read_json(work, f'ls-decide-{case}.json'))}"
                for case, (decision, *_) in DECISION_MATRIX.items()
                if decision == "refuse"
            )
            + f"; baseline {sandbox_names(read_json(work, 'ls-baseline.json'))}; "
            f"failures {untouched_failures(obs, work) or 'none'}",
            ready,
        )
    )
    rows.append(
        (
            "G10.a3",
            "ignored files alone never refuse a run (control: only the ignored canary present)",
            ignored_only_failures(obs) == [],
            f"ignored-only decision {obs.get('decide_ignored_only_decision', '-')}/exit "
            f"{obs.get('decide_ignored_only_exit', '-')}, canary present "
            f"{obs.get('ignored_only_env_present', '-')} and ignored by the fixture "
            f"{obs.get('ignored_only_env_is_ignored', '-')}; "
            f"failures {ignored_only_failures(obs) or 'none'}",
            ready,
        )
    )
    phase_a_holds = all(row[2] for row in rows if row[0] in PHASE_A)

    # --- Phase B -------------------------------------------------------------------------------
    rows.append(
        (
            "G10.b1",
            "Phase B starts dirty under the explicit override, and exactly the selected ref is bundled",
            bundle_failures(obs, work) == [],
            f"override: {obs.get('override_ignore_uncommitted', 'not recorded')}; starting state "
            f"tracked {obs.get('phase_b_tracked', '-')} / untracked {obs.get('phase_b_untracked', '-')} "
            f"/ ignored mentioned {obs.get('phase_b_mentions_ignored', '-')}; ref "
            f"{obs.get('selected_ref', '-')} at {obs.get('selected_ref_commit', '-')}; "
            f"verify exit {obs.get('bundle_verify_exit', '-')}; advertised heads "
            f"{obs.get('bundle_head_count', '-')}: '{obs.get('bundle_head_line', '-')}'; "
            f"failures {bundle_failures(obs, work) or 'none'}",
            ready and phase_a_holds,
        )
    )
    rows.append(
        (
            "G10.b2",
            "mountless sandbox with --skills=off from the exact pinned Claude base",
            obs.get(f"create_{SANDBOX}_exit") == "0"
            and obs.get("cp_exit") == "0"
            and "no workspace bind mount" in obs.get(f"workspace_line_{SANDBOX}", "")
            and image == pinned_base
            and obs.get("template_ls_exit") == "0"
            and preflight_module.template_identity_matches(cached, pinned_base, pinned_digest),
            f"create exit {obs.get(f'create_{SANDBOX}_exit', '-')}, sbx cp exit "
            f"{obs.get('cp_exit', '-')}, sbx resolved {image or 'not reported'} (pin {pinned_base}), "
            f"cached template {(cached or {}).get('repository', 'not found')} tag "
            f"{(cached or {}).get('tag', '-')} image id {(cached or {}).get('id', '-')} prefixing "
            f"{pinned_digest}, workspace '{obs.get(f'workspace_line_{SANDBOX}', 'not reported')}'",
            ready and phase_a_holds,
        )
    )
    rows.append(
        (
            "G10.b3",
            "the VM holds only the selected committed state: no host mount, no dirty canary",
            probed and delivery_failures(probe, obs) == [],
            f"/run/sandbox/source exists {probe.get('source_mount_path_exists', '-')}, workspace "
            f"mounts {probe.get('workspace_mounts', '-')} of {probe.get('mount_total', '-')} "
            f"(host-backed: {probe.get('host_fs_mount_targets', '').strip() or 'none'}), canary hits "
            f"env {probe.get('env_canary_hits', '-')} / untracked {probe.get('untracked_canary_hits', '-')} "
            f"/ dirty-edit {probe.get('dirty_canary_hits', '-')} / baseline "
            f"{probe.get('baseline_canary_hits', '-')}, delivered head {probe.get('head_commit', '-')}; "
            f"failures {delivery_failures(probe, obs) or 'none'}",
            probed,
        )
    )
    rows.append(
        (
            "G10.b4",
            "the second branch is absent by identity: no ref, no unique commit object, no canary",
            probed and second_branch_failures(probe) == [],
            f"unique commit {obs.get('second_branch_commit', '-')} present in VM "
            f"{probe.get('second_commit_present', '-')} (git cat-file -e), second-branch refs "
            f"{probe.get('second_ref_hits', '-')}, in git rev-list --all "
            f"{probe.get('second_in_rev_list', '-')}, branch-only canary hits "
            f"{probe.get('second_canary_hits', '-')}, delivered refs "
            f"'{probe.get('ref_names', '').strip()}'; second branch is an ancestor of selected: "
            f"{obs.get('second_is_ancestor', '-')}; failures {second_branch_failures(probe) or 'none'}",
            probed,
        )
    )

    remaining = read_json(work, "ls-after.json")
    left = sandbox_names(remaining)
    rows.append(
        (
            "G10.c1",
            "the G10 sandbox was removed by name and none remains",
            # A run stopped before Phase B never created the sandbox, so `rm` failing on a
            # missing name is not a cleanup failure; a remaining sandbox always is.
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
        "gate": "G10",
        "status": status,
        "run_at": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "versions": {
            "sbx": (read_json(work, "pf-version.json") or {}).get("client", {}).get("version") or None
        },
        "provenance": rules_module.evidence_provenance("G10", versions),
        "criteria": criteria,
        "fallback_applied": None,
        "notes": (
            "Two independent properties, neither weakened for the other. Phase A: with no "
            "override the dirty tree refuses before any bundle or sandbox exists, and a control "
            "shows ignored files alone never refuse. Phase B: under the explicitly recorded "
            "gate-local --ignore-uncommitted equivalent, only refs/heads/" + SELECTED_BRANCH +
            " is bundled, verified and delivered by sbx cp into one mountless --skills=off "
            "sandbox from the pinned Claude base. The second branch is proven absent by identity "
            "(no ref, git cat-file -e on its unique commit fails, not in git rev-list --all, "
            "canary absent), not by wording. Phase A mirrors launcher-cli precondition 2 with a "
            "gate-local observation; the launcher and its ref parser are T064. Canary values are "
            "never recorded: the evidence carries IDs and match counts only. The sandbox is "
            "removed by name; sbx rm --all and sbx reset are never used."
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
    if path is None or mode not in (None, "--preflight", "--phase-a"):
        print("usage: python3 gates/G10/record.py [--preflight|--phase-a] <observations.env> [<work-dir>]",
              file=sys.stderr)
        return 2
    obs = read_observations(path)
    if mode == "--preflight":
        rows = preflight(obs, work)
        for identifier, description, result, observed in rows:
            print(f"  {identifier} {'PASS' if result else 'FAIL'} {description}")
            print(f"          observed: {observed}")
        return 0 if all(result for _, _, result, _ in rows) else 1
    if mode == "--phase-a":
        results = {row["id"]: row["result"] for row in evaluate(obs, work)}
        for identifier in PHASE_A:
            print(f"  {identifier} {results[identifier]}")
        return 0 if all(results[identifier] == "PASS" for identifier in PHASE_A) else 1

    status, criteria = record(path, work)
    print(f"G10: {status} ({EVIDENCE})")
    for row in criteria:
        print(f"  {row['id']:<9} {row['result']:<8} {row['description']}")
        print(f"          observed: {row['evidence_ref']}")
    return 0 if status == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
