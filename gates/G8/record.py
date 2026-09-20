"""Evaluate the G8 observations and write gates/G8.json (tasks.md T011).

Standard library only, and nothing global changes. G8 proves Docker Sandboxes shared-skills
isolation at the current probe stage:

- a sandbox created with --skills=off has no shared skills-store mount;
- no host or shared skill appears in any skill directory found in the VM;
- with the G6 probe kit, the kit's skill directory holds exactly dca-probe-one and
  dca-probe-two, and every other skill directory is empty;
- no speckit-* construction skill is present anywhere.

It does NOT prove the final four runtime skills: those arrive with T047-T050 and the production
kit (T056), and are proven by production conformance (T062).

Skill directories are discovered in the VM rather than assumed, and the evaluator fails closed:
a missing probe field, an unparsable listing, an unexpected entry or an undiscovered kit
directory fails its criterion.

Usage:
  python3 gates/G8/record.py <observations.env> [<work-dir>]   write gates/G8.json
  python3 gates/G8/record.py --preflight <obs> [<work-dir>]    exit 0 only if the post-G0 state
                                                               still holds
"""

import datetime
import importlib.util
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
WORK = os.path.join(ROOT, "gates", "G8", "work")
VERSIONS = os.path.join(ROOT, "runtime", "versions.yaml")
EVIDENCE = os.path.join(ROOT, "gates", "G8.json")

KIT_SKILLS_DIR = "/opt/dca/skills"
PROBE_SKILLS = ("dca-probe-one", "dca-probe-two")
BACKENDS = (("claude", "dca-g8-claude"), ("codex", "dca-g8-codex"))
PREFLIGHT = ("G8.0a", "G8.0b", "G8.0c")


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


def skill_directories(probe):
    """[(path, entries, mount count)] as discovered in the VM, or None when not reported."""
    if probe.get("probe_complete") != "yes" or not probe.get("skills_dir_count", "").isdigit():
        return None
    out = []
    for index in range(1, int(probe["skills_dir_count"]) + 1):
        path = probe.get(f"skills_dir_{index}")
        mounted = probe.get(f"skills_dir_{index}_mounted")
        if not path or not (mounted or "").isdigit():
            return None
        out.append((path, sorted(probe.get(f"skills_dir_{index}_entries", "").split()), int(mounted)))
    return out


def shared_store_failures(probe):
    """Why the shared skills store might be reachable, as reasons (empty = it isn't)."""
    directories = skill_directories(probe)
    if directories is None:
        return ["the probe did not report a usable skill-directory listing"]
    reasons = []
    targets = probe.get("mount_skill_targets", "").split()
    if targets:
        reasons.append(f"mount targets under a skills path: {' '.join(targets)}")
    for path, _, mounted in directories:
        if mounted:
            reasons.append(f"{path} is a mount point ({mounted})")
    if not (probe.get("mount_total") or "").isdigit():
        reasons.append("the probe did not report the mount table")
    return reasons


def skill_content_failures(probe):
    """Why the skill directories hold something other than exactly the G6 probe skills."""
    directories = skill_directories(probe)
    if directories is None:
        return ["the probe did not report a usable skill-directory listing"]
    reasons = []
    kit = [entries for path, entries, _ in directories if path == KIT_SKILLS_DIR]
    if not kit:
        reasons.append(f"the kit skill directory {KIT_SKILLS_DIR} was not found in the VM")
    elif kit[0] != sorted(PROBE_SKILLS):
        reasons.append(f"{KIT_SKILLS_DIR} holds {kit[0]}, not exactly {sorted(PROBE_SKILLS)}")
    for path, entries, _ in directories:
        if path != KIT_SKILLS_DIR and entries:
            reasons.append(f"{path} is not empty: {entries}")
    if probe.get("speckit_hits") != "0":
        reasons.append(f"speckit-* paths found: {probe.get('speckit_hits', 'not reported')}")
    return reasons


def preflight(obs, work):
    with open(VERSIONS, encoding="utf-8") as fh:
        pins = json.load(fh)
    version = read_json(work, "pf-version.json")
    forwarding = read_json(work, "pf-ssh-forwarding.json")
    socket_path = read_json(work, "pf-ssh-socket.json")
    policy = read_json(work, "pf-policy.json")
    refusals = preflight_module.ssh_forwarding_refusals(forwarding, socket_path)

    return [
        (
            "G8.0a",
            "sbx client and server still match the pinned exact version and the server is running",
            obs.get("pf_version_exit") == "0"
            and preflight_module.version_pins_hold(version, pins["sbx"]["exact"]),
            f"pin {pins['sbx']['exact']}, client {(version or {}).get('client', {}).get('version')}, "
            f"server {(version or {}).get('server', {}).get('version')} state "
            f"{(version or {}).get('server', {}).get('state')}, exit {obs.get('pf_version_exit', '-')}",
        ),
        (
            "G8.0b",
            "the accepted SSH baseline is unchanged: forwarding false and no fixed agent socket",
            obs.get("pf_ssh_forwarding_exit") == "0"
            and obs.get("pf_ssh_socket_exit") == "0"
            and refusals == []
            and obs.get("sbx_env_ssh_auth_sock") == "removed",
            f"{preflight_module.SSH_FORWARDING_KEY}="
            f"{preflight_module.setting_value(forwarding, preflight_module.SSH_FORWARDING_KEY)!r}, "
            f"{preflight_module.SSH_SOCKET_KEY}="
            f"{preflight_module.setting_value(socket_path, preflight_module.SSH_SOCKET_KEY)!r}, "
            f"detector refusals {refusals or 'none'}, host SSH_AUTH_SOCK as seen by sbx "
            f"{obs.get('sbx_env_ssh_auth_sock', '-')}",
        ),
        (
            "G8.0c",
            "global network policy is still exactly the post-G0 bootstrap baseline (unrepaired)",
            obs.get("pf_policy_exit") == "0"
            and policy is not None
            and preflight_module.network_baseline_holds(policy),
            f"exit {obs.get('pf_policy_exit', '-')}, "
            f"{len(preflight_module.network_rules(policy))} network rule(s) "
            f"{[{k: r.get(k) for k in preflight_module.BOOTSTRAP_RULE} for r in preflight_module.network_rules(policy)]}",
        ),
    ]


def evaluate(obs, work):
    rows = [(i, d, r, o, True) for i, d, r, o in preflight(obs, work)]
    ready = all(row[2] for row in rows)

    with open(VERSIONS, encoding="utf-8") as fh:
        pins = json.load(fh)
    templates = read_json(work, "templates.json")
    host_store = read_json(work, "host-skills.json")
    host_skills = (host_store or {}).get("skills")
    # Whether any base's control mounted the store, which decides how a base with no mount is
    # explained: the same host store mounting elsewhere rules out store-emptiness.
    mounted_anywhere = [
        other
        for _, other_sandbox in BACKENDS
        for other in [f"{other_sandbox}-control"]
        if (read_env(work, f"probe-{other}.env").get("mount_skill_targets") or "").strip()
    ]

    for backend, sandbox in BACKENDS:
        control = f"{sandbox}-control"
        probe = read_env(work, f"probe-{sandbox}.env")
        control_probe = read_env(work, f"probe-{control}.env")
        created = obs.get(f"create_{sandbox}_exit") == "0"
        probed = obs.get(f"probe_{sandbox}_exit") == "0"
        image = obs.get(f"image_{sandbox}") or ""
        workspace_line = obs.get(f"workspace_line_{sandbox}", "")
        pinned_base = pins["sandbox_bases"][backend]["base"]
        pinned_digest = pins["sandbox_bases"][backend]["version"]
        repository, _, tag = (pinned_base or "").rpartition(":")
        cached = preflight_module.template_image(templates, repository, tag)
        directories = skill_directories(probe)
        control_directories = skill_directories(control_probe)
        control_mounts = (
            [path for path, _, mounted in control_directories if mounted]
            if control_directories is not None
            else None
        )

        rows.append(
            (
                f"G8.{backend}.create",
                f"{backend}: mountless sandbox with --skills=off from the exact pinned base",
                created
                and probed
                and "no workspace bind mount" in workspace_line
                and image == pinned_base
                and obs.get("template_ls_exit") == "0"
                and preflight_module.template_identity_matches(cached, pinned_base, pinned_digest),
                f"create exit {obs.get(f'create_{sandbox}_exit', '-')}, probe exit "
                f"{obs.get(f'probe_{sandbox}_exit', '-')}, sbx resolved {image or 'not reported'} "
                f"(pin {pinned_base}), cached template {(cached or {}).get('repository', 'not found')} "
                f"tag {(cached or {}).get('tag', '-')} image id {(cached or {}).get('id', '-')} "
                f"prefixing the pinned digest {pinned_digest}, workspace "
                f"'{workspace_line or 'not reported'}'",
                ready,
            )
        )
        rows.append(
            (
                f"G8.{backend}.store",
                f"{backend}: the Docker Sandboxes shared skills store is not mounted",
                probed and shared_store_failures(probe) == [],
                f"{probe.get('mount_total', '-')} mounts, none under a skills path "
                f"({probe.get('mount_skill_targets', '').strip() or 'no skill-path mount targets'}); "
                f"discovered skill directories "
                f"{[(p, m) for p, _, m in directories] if directories is not None else 'not reported'}; "
                f"failures {shared_store_failures(probe) or 'none'}",
                probed,
            )
        )
        rows.append(
            (
                f"G8.{backend}.skills",
                f"{backend}: exactly the two G6 probe skills, and no other skill anywhere",
                probed and skill_content_failures(probe) == [],
                f"{KIT_SKILLS_DIR}: {probe.get('kit_skills_entries', 'not reported').strip()}; "
                f"other discovered directories "
                f"{[(p, e) for p, e, _ in directories if p != KIT_SKILLS_DIR] if directories is not None else 'not reported'}; "
                f"speckit-* paths {probe.get('speckit_hits', 'not reported')}; "
                f"failures {skill_content_failures(probe) or 'none'}",
                probed,
            )
        )
        rows.append(
            (
                f"G8.{backend}.control",
                f"{backend}: a --skills=readonly control records whether a store mount is observable here",
                obs.get(f"create_{control}_exit") == "0"
                and obs.get(f"probe_{control}_exit") == "0"
                and control_directories is not None,
                f"control sandbox {control}: create exit {obs.get(f'create_{control}_exit', '-')}, "
                f"probe exit {obs.get(f'probe_{control}_exit', '-')}, skills-path mount targets "
                f"'{control_probe.get('mount_skill_targets', '').strip() or 'none'}', mounted skill "
                f"directories {control_mounts if control_mounts is not None else 'not reported'}; "
                f"host shared store holds "
                f"{len(host_skills) if isinstance(host_skills, list) else 'an unreported number of'} skills"
                + (
                    ". The mount is observable at readonly and absent at off, so the "
                    "--skills=off observation for this base is a differential, not a bare negative"
                    if control_mounts
                    else (
                        f". No skills-path mount appeared even at readonly, while {mounted_anywhere} "
                        "mounted one from the same host store, so this is base-specific rather than "
                        "an artifact of the store being empty: this base exposes no agent skills "
                        "directory for the store to mount into. The --skills=off result for this "
                        "base is therefore a bare negative, corroborated by the absence of any "
                        "skill directory other than the kit's"
                        if mounted_anywhere
                        else ". No skills-path mount appeared at readonly on any base, so this run "
                        "does not demonstrate that the check would catch a populated store; proving "
                        "that needs a seeded canary, a separately authorized step, or the final "
                        "assets in production conformance (T062)"
                    )
                ),
                ready,
            )
        )
        rows.append(
            (
                f"G8.{backend}.cleanup",
                f"{backend}: both sandboxes were removed by name",
                obs.get(f"rm_{sandbox}_exit") == "0" and obs.get(f"rm_{control}_exit") == "0",
                f"sbx rm --force {sandbox} exit {obs.get(f'rm_{sandbox}_exit', '-')}, "
                f"{control} exit {obs.get(f'rm_{control}_exit', '-')}",
                f"rm_{sandbox}_exit" in obs,
            )
        )

    remaining = read_json(work, "ls-after.json")
    names = {name for _, sandbox in BACKENDS for name in (sandbox, f"{sandbox}-control")}
    left = (
        [e for e in (remaining or {}).get("sandboxes", []) if isinstance(e, dict) and e.get("name") in names]
        if isinstance(remaining, dict)
        else None
    )
    rows.append(
        (
            "G8.clean",
            "no G8 sandbox remains",
            left == [],
            f"sbx ls --json after cleanup: "
            f"{len((remaining or {}).get('sandboxes', [])) if isinstance(remaining, dict) else 'unreadable'} "
            f"sandboxes, of which G8's: {left if left is not None else 'unreadable'}",
            "ls_after_exit" in obs,
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
        "gate": "G8",
        "status": status,
        "run_at": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "versions": {
            "sbx": (read_json(work, "pf-version.json") or {}).get("client", {}).get("version") or None
        },
        "provenance": rules_module.evidence_provenance("G8", versions),
        "criteria": criteria,
        "fallback_applied": None,
        "notes": (
            "Verification only: G8 changes nothing global. Both pinned bases are covered, because "
            "the shared store is mounted at the agent's skills directory, which is agent-specific. "
            "Skill directories are discovered in the VM, not assumed. Each base also gets a "
            "--skills=readonly control sandbox, so the --skills=off result is read against what a "
            "mounted store would look like here. G8 proves shared-store isolation at the probe "
            "stage with the G6 kit; the final four runtime skills are a later property of the "
            "production kit (T056) and production conformance (T062), not of this gate. All four "
            "sandboxes are mountless, named and removed by name; sbx rm --all and sbx reset are "
            "never used."
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
    if path is None or mode not in (None, "--preflight"):
        print("usage: python3 gates/G8/record.py [--preflight] <observations.env> [<work-dir>]",
              file=sys.stderr)
        return 2
    if mode == "--preflight":
        rows = preflight(read_observations(path), work)
        for identifier, description, result, observed in rows:
            print(f"  {identifier} {'PASS' if result else 'FAIL'} {description}")
            print(f"          observed: {observed}")
        return 0 if all(result for _, _, result, _ in rows) else 1

    status, criteria = record(path, work)
    print(f"G8: {status} ({EVIDENCE})")
    for row in criteria:
        print(f"  {row['id']:<20} {row['result']:<8} {row['description']}")
        print(f"          observed: {row['evidence_ref']}")
    return 0 if status == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
