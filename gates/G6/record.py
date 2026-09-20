"""Evaluate the G6 observations and write gates/G6.json (tasks.md T009).

Standard library only. It reads what gates/G6/run.sh captured and decides every criterion from
machine-readable output plus exit status. Shapes come from the sbx v0.43.0 pinned by G0; they
are empirically observed, not a promised schema, and an sbx upgrade re-runs the gates (R27).

Fail-closed: a missing file, malformed JSON, an unexpected shape, an absent field or an
unsuccessful command fails its criterion, and the pins G6 owns are written only when every
other criterion passed.

Usage:
  python3 gates/G6/record.py <observations.env> [<work-dir>]   write gates/G6.json (+ pins)
  python3 gates/G6/record.py --preflight <obs> [<work-dir>]    exit 0 only if the global state
                                                               G0 established still holds
"""

import datetime
import importlib.util
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
WORK = os.path.join(ROOT, "gates", "G6", "work")
VERSIONS = os.path.join(ROOT, "runtime", "versions.yaml")
EVIDENCE = os.path.join(ROOT, "gates", "G6.json")

def _load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# The post-G0 state checks and the base-identity rules are shared by every gate after G0
# (gates/preflight.py); G6 keeps these names so its evidence and tests stay stable.
preflight_module = _load_module("dca_preflight", os.path.join(ROOT, "gates", "preflight.py"))
BOOTSTRAP_RULE = preflight_module.BOOTSTRAP_RULE
bootstrap_rule_matches = preflight_module.bootstrap_rule_matches
template_identity_matches = preflight_module.template_identity_matches

DOCKER_AGENT_VERSION = "v1.136.0"
SSH_KEY = "ssh.agentForwardingEnabled"
BACKENDS = (("claude", "dca-g6-claude"), ("codex", "dca-g6-codex"))
PREFLIGHT = ("G6.0a", "G6.0b", "G6.0c")
ARTIFACT_ASSET = "docker-agent-linux-arm64"
ARTIFACT_URL = (
    f"https://github.com/docker/docker-agent/releases/download/{DOCKER_AGENT_VERSION}/{ARTIFACT_ASSET}"
)
# How strongly the artifact pin is backed, weakest last:
#   publisher-signature        a Docker signature or attestation over the artifact
#   release-asset-digest       the digest the official release metadata publishes for the asset
#   recorded-reproducibility-pin  only the hash this gate computed
ARTIFACT_VERIFICATION = "release-asset-digest"
# `sbx ls --json` carries no base image (observed: name, id, agent, status, last_used_at), so
# the base comes from the RESOLVE SETUP block sbx prints on create, and its exact version from
# the registry digest that tag pointed at when the gate ran.
NO_WORKSPACE_MOUNT = "no workspace bind mount"


def load_rules():
    path = os.path.join(ROOT, "gates", "eligibility_rules.py")
    spec = importlib.util.spec_from_file_location("eligibility_rules", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def read_observations(path):
    observations = {}
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            key, sep, value = line.rstrip("\n").partition("=")
            if sep:
                observations[key] = value
    return observations


def read_json(work, name):
    try:
        with open(os.path.join(work, name), encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return None


def read_env(work, name):
    """key=value lines captured from inside a sandbox."""
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


def parse_version(text):
    digits = ""
    for char in str(text).strip().lstrip("v"):
        if char.isdigit() or char == ".":
            digits += char
        elif digits:
            break
    parts = [int(p) for p in digits.split(".") if p]
    return tuple(parts) if parts else None


def sandboxes(listing):
    """The sandbox entries of an `sbx ls --json` document, or None for an unexpected shape."""
    if isinstance(listing, dict) and set(listing) == {"sandboxes"} and isinstance(listing["sandboxes"], list):
        return listing["sandboxes"]
    return None


def sandbox_entry(work, name, sandbox_name):
    entries = sandboxes(read_json(work, name))
    for entry in entries or []:
        if isinstance(entry, dict) and entry.get("name") == sandbox_name:
            return entry
    return None


def base_identifier(obs, sandbox, work=None):
    """(reference sbx resolved, registry digest, cached template image) for a sandbox.

    The reference comes from sbx's own RESOLVE SETUP block, the digest from the registry at
    gate time, and the cached image from `sbx template ls --json`, whose short id ties the
    image sbx actually used to that digest.
    """
    image = obs.get(f"image_{sandbox}") or None
    digest = obs.get(f"base_digest_{sandbox}") or None
    if digest and not (digest.startswith("sha256:") and len(digest) == 71):
        digest = None
    repository, _, tag = (image or "").rpartition(":")
    cached = template_image(work, repository, tag) if (work and repository and tag) else None
    return image, digest, cached


def release_asset(work):
    """The docker-agent release asset from the official GitHub release metadata, or None."""
    release = read_json(work, "release.json")
    for asset in (release or {}).get("assets", []) or []:
        if isinstance(asset, dict) and asset.get("name") == ARTIFACT_ASSET:
            return asset
    return None


def template_image(work, repository, tag):
    """The cached sbx template with this exact repository and tag, or None (shared rule)."""
    return preflight_module.template_image(read_json(work, "templates.json"), repository, tag)


def preflight(obs, work):
    """The read-only recheck of the global state G0 established."""
    with open(VERSIONS, encoding="utf-8") as fh:
        pins = json.load(fh)
    version = read_json(work, "pf-version.json") or {}
    client = (version.get("client") or {}).get("version")
    server = version.get("server") or {}
    ssh = read_json(work, "pf-ssh.json") or {}
    policy = read_json(work, "pf-policy.json")
    rules = [r for r in (policy or {}).get("rules", []) if isinstance(r, dict)]
    network = [r for r in rules if str(r.get("resource_type", "")).startswith("network")]
    baseline = len(network) == 1 and bootstrap_rule_matches(network[0])

    return [
        (
            "G6.0a",
            "sbx client and server still match the pinned exact version and the server is running",
            obs.get("pf_version_exit") == "0"
            and client == pins["sbx"]["exact"]
            and server.get("version") == pins["sbx"]["exact"]
            and server.get("state") == "running",
            f"pin {pins['sbx']['exact']}, client {client}, server {server.get('version')} "
            f"state {server.get('state')}, exit {obs.get('pf_version_exit', '-')}",
        ),
        (
            "G6.0b",
            f"global {SSH_KEY} is still false (G0)",
            obs.get("pf_ssh_exit") == "0" and ssh.get("key") == SSH_KEY and ssh.get("value") is False,
            f"value={ssh.get('value')!r}, source={ssh.get('source')}, exit {obs.get('pf_ssh_exit', '-')}",
        ),
        (
            "G6.0c",
            "global network policy is still in the post-G0 state (deny-all bootstrap, unrepaired)",
            obs.get("pf_policy_exit") == "0" and policy is not None and baseline,
            f"exit {obs.get('pf_policy_exit', '-')}, {len(network)} network rule(s) "
            f"{[{k: r.get(k) for k in BOOTSTRAP_RULE} for r in network]} (the bootstrap rule "
            "must be the only one; filesystem rules are separate)",
        ),
    ]


def evaluate(obs, work):
    """Return G6's criteria, in execution order."""
    rows = [(i, d, r, o) for i, d, r, o in preflight(obs, work)]
    ready = all(result for _, _, result, _ in rows)

    artifact_sha = obs.get("artifact_sha256") or ""
    asset = release_asset(work)
    asset_digest = (asset or {}).get("digest")
    rows.append(
        (
            "G6.1",
            f"the pinned docker-agent {DOCKER_AGENT_VERSION} artifact is fetched and hashed on the host",
            len(artifact_sha) == 64 and all(c in "0123456789abcdef" for c in artifact_sha)
            and obs.get("artifact_bytes", "0").isdigit()
            and int(obs.get("artifact_bytes", "0")) > 0,
            f"{obs.get('artifact_url')} sha256={artifact_sha or 'none'} "
            f"bytes={obs.get('artifact_bytes', '-')}",
        )
    )
    rows.append(
        (
            "G6.1b",
            "the artifact matches the official GitHub release metadata (URL, size, asset digest)",
            obs.get("release_metadata_exit") == "0"
            and asset is not None
            and asset.get("browser_download_url") == ARTIFACT_URL
            and str(asset.get("size")) == obs.get("artifact_bytes")
            and isinstance(asset_digest, str)
            and asset_digest == f"sha256:{artifact_sha}",
            f"release asset {ARTIFACT_ASSET}: url "
            f"{'matches' if asset and asset.get('browser_download_url') == ARTIFACT_URL else asset.get('browser_download_url') if asset else 'not found'}, "
            f"size {asset.get('size') if asset else '-'} vs downloaded {obs.get('artifact_bytes', '-')}, "
            f"digest {asset_digest or 'absent'} vs computed sha256:{artifact_sha or 'none'}; "
            f"this is a release-hosting asset digest, not a Docker signature or attestation "
            f"(gh attestation verify exit {obs.get('artifact_attestation_exit', '-')}: none published)",
        )
    )
    rows.append(
        (
            "G6.2",
            "the probe kit validates and stages only host-provided files (no sandbox egress)",
            obs.get("kit_validate_exit") == "0",
            f"sbx kit validate exit {obs.get('kit_validate_exit', '-')}; kit declares no "
            "permissions.network and no remote kit source (kit.allowLocalKits default)",
        )
    )

    for backend, sandbox in BACKENDS:
        probe = read_env(work, f"probe-{sandbox}.env")
        entry = sandbox_entry(work, f"ls-{sandbox}.json", sandbox)
        base, base_version, cached = base_identifier(obs, sandbox, work)
        workspace_line = obs.get(f"workspace_line_{sandbox}", "")
        created = obs.get(f"create_{sandbox}_exit") == "0"
        probed = obs.get(f"probe_{sandbox}_exit") == "0"
        python_version = parse_version(probe.get("python3_version", ""))

        rows.append(
            (
                f"G6.{backend}.create",
                f"{backend}: mountless sandbox from the {backend} base, shared skills off",
                created
                and probed
                and entry is not None
                and NO_WORKSPACE_MOUNT in workspace_line
                and probe.get("workspace_mounted") == "0"
                and probe.get("workspace_entries") == "0"
                and probe.get("skills_store_mounted") == "0",
                f"create exit {obs.get(f'create_{sandbox}_exit', '-')}, probe exit "
                f"{obs.get(f'probe_{sandbox}_exit', '-')}, sbx resolved workspace "
                f"'{workspace_line or 'not reported'}', mounts at the workspace path "
                f"{probe.get('workspace_mounted', '-')}, workspace entries "
                f"{probe.get('workspace_entries', '-')}, mounts at the shared skills path "
                f"{probe.get('skills_store_mounted', '-')}, other host-backed mounts "
                f"(microVM plumbing) {probe.get('host_fs_mount_targets', '-').strip() or 'none'}, "
                f"os {probe.get('os_release', '-')}",
            )
        )
        rows.append(
            (
                f"G6.{backend}.kit",
                f"{backend}: kit files installed (artifact, probe file, manifest, managed settings, two skills)",
                probed
                and probe.get("docker_agent_version") == DOCKER_AGENT_VERSION
                and probe.get("docker_agent_sha256") == artifact_sha
                and probe.get("kit_probe") == "yes"
                and probe.get("kit_manifest") == "yes"
                and probe.get("managed_settings") == "yes"
                and probe.get("skill_one") == "yes"
                and probe.get("skill_two") == "yes",
                f"docker-agent {probe.get('docker_agent_version', '-')} sha256 "
                f"{'matches the host artifact' if probe.get('docker_agent_sha256') == artifact_sha else probe.get('docker_agent_sha256', 'none')}, "
                f"probe file {probe.get('kit_probe', '-')}, manifest {probe.get('kit_manifest', '-')}, "
                f"managed settings {probe.get('managed_settings', '-')}, skills "
                f"{probe.get('skill_one', '-')}/{probe.get('skill_two', '-')}",
            )
        )
        rows.append(
            (
                f"G6.{backend}.python",
                f"{backend}: /usr/bin/python3 exists and is >= 3.11",
                probed
                and probe.get("python3_path") == "/usr/bin/python3"
                and probe.get("python3_min_exit") == "0"
                and python_version is not None
                and python_version >= (3, 11),
                f"{probe.get('python3_path', '-')} version {probe.get('python3_version', '-')}, "
                f"version check exit {probe.get('python3_min_exit', '-')}",
            )
        )
        rows.append(
            (
                f"G6.{backend}.base",
                f"{backend}: the exact sandbox base is recorded and the cached template matches it",
                bool(base) and bool(base_version) and template_identity_matches(cached, base, base_version),
                f"sbx_resolved_base {base}; registry_digest_at_gate_time {base_version}; "
                f"cached template repository {cached.get('repository')} tag {cached.get('tag')} "
                f"image id {cached.get('id')} (sbx template ls --json; a short image id, not a "
                "digest on its own), whose id prefixes that registry digest, so the three "
                "together name the image sbx actually used"
                if base and base_version and template_identity_matches(cached, base, base_version)
                else f"sbx_resolved_base {base or 'not reported'}; registry_digest_at_gate_time "
                f"{base_version or 'not resolved'}; cached template "
                f"{cached.get('id') if cached else 'not found in sbx template ls --json'}",
            )
        )
        rows.append(
            (
                f"G6.{backend}.cleanup",
                f"{backend}: the probe sandbox was removed by name",
                obs.get(f"rm_{sandbox}_exit") == "0",
                f"sbx rm --force {sandbox} exit {obs.get(f'rm_{sandbox}_exit', '-')}",
            )
        )

    rows.append(
        (
            "G6.templates",
            "the local sbx template store was read (read-only) to identify the bases",
            obs.get("template_ls_exit") == "0" and isinstance(read_json(work, "templates.json"), dict),
            f"sbx template ls --json exit {obs.get('template_ls_exit', '-')}, "
            f"{len((read_json(work, 'templates.json') or {}).get('images', []))} cached templates",
        )
    )

    remaining = sandboxes(read_json(work, "ls-after.json"))
    rows.append(
        (
            "G6.clean",
            "no G6 sandbox remains",
            remaining is not None
            and not [e for e in remaining if isinstance(e, dict) and e.get("name") in dict(BACKENDS).values()],
            f"sbx ls --json after cleanup: {len(remaining) if remaining is not None else 'unreadable'} sandboxes",
        )
    )
    rows.append(
        (
            "G6.pins",
            "docker_agent_artifact and both sandbox_bases written into runtime/versions.yaml",
            obs.get("pins_written") == "true",
            "written" if obs.get("pins_written") == "true" else "not written",
        )
    )

    criteria = []
    reached = ready
    for identifier, description, result, observed in rows:
        if identifier in PREFLIGHT:
            outcome = "PASS" if result else "FAIL"
        elif reached:
            outcome = "PASS" if result else "FAIL"
            if not result:
                reached = False
        else:
            outcome = "NOT-RUN"
            observed = "an earlier step did not hold, so the gate stopped before this one (fail-closed)"
        criteria.append(
            {"id": identifier, "description": description, "result": outcome, "evidence_ref": observed}
        )
    return criteria


def write_pins(obs, work, versions_path=VERSIONS):
    """Write only the pins G6 owns, keeping the canonical JSON-compatible YAML form."""
    with open(versions_path, encoding="utf-8") as fh:
        versions = json.load(fh)
    versions["docker_agent_artifact"] = {
        "sha256": obs["artifact_sha256"],
        "url": obs["artifact_url"],
        # Verified against the digest the official GitHub release metadata publishes for this
        # asset. That is release-hosting integrity, not a Docker signature or attestation.
        "verification": ARTIFACT_VERIFICATION,
    }
    for backend, sandbox in BACKENDS:
        base, base_version, _ = base_identifier(obs, sandbox, work)
        versions["sandbox_bases"][backend] = {"base": base, "version": base_version}
    with open(versions_path, "w", encoding="utf-8") as fh:
        fh.write(json.dumps(versions, indent=2, sort_keys=True, ensure_ascii=False) + "\n")
    return versions


def record(obs_path, work=WORK, versions_path=VERSIONS, evidence_path=EVIDENCE):
    rules = load_rules()
    obs = read_observations(obs_path)
    criteria = evaluate(obs, work)

    if all(row["result"] == "PASS" for row in criteria if row["id"] != "G6.pins"):
        write_pins(obs, work, versions_path)
        with open(obs_path, "a", encoding="utf-8") as fh:
            fh.write("pins_written=true\n")
        obs = read_observations(obs_path)
        criteria = evaluate(obs, work)

    status = "PASS" if all(row["result"] == "PASS" for row in criteria) else "FAIL"
    with open(versions_path, encoding="utf-8") as fh:
        versions = json.load(fh)
    evidence = {
        "gate": "G6",
        "status": status,
        "run_at": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "versions": {
            "sbx": (read_json(work, "pf-version.json") or {}).get("client", {}).get("version") or None,
            "docker_agent": DOCKER_AGENT_VERSION,
        },
        "provenance": rules.evidence_provenance("G6", versions),
        "criteria": criteria,
        "fallback_applied": None,
        "notes": (
            "Observed by gates/G6/run.sh. Both sandboxes were mountless (no workspace path), "
            "created with --skills off, explicitly named and removed by name; sbx rm --all was "
            "never used. The probe kit is a local directory kit that stages the host-fetched "
            "docker-agent artifact, so it declares no network permission and the sandboxes "
            "needed no egress under the G0 deny-all bootstrap policy. The artifact pin is "
            "verified against the digest the official GitHub release metadata publishes for "
            "the asset (release-asset-digest): release-hosting integrity, not a Docker "
            "signature or attestation, of which none is published for this artifact."
        ),
    }
    if status != "PASS":
        evidence["notes"] += " Fail-closed: no pin was written for a gate that did not pass."
    with open(evidence_path, "w", encoding="utf-8") as fh:
        fh.write(json.dumps(evidence, indent=2, ensure_ascii=False) + "\n")
    return status, criteria


def main(argv):
    args = argv[1:]
    mode = args.pop(0) if args and args[0].startswith("--") else None
    path = args[0] if args else None
    work = args[1] if len(args) > 1 else WORK
    if path is None or mode not in (None, "--preflight"):
        print("usage: python3 gates/G6/record.py [--preflight] <observations.env> [<work-dir>]",
              file=sys.stderr)
        return 2
    if mode == "--preflight":
        rows = preflight(read_observations(path), work)
        for identifier, description, result, observed in rows:
            print(f"  {identifier} {'PASS' if result else 'FAIL'} {description}")
            print(f"          observed: {observed}")
        return 0 if all(result for _, _, result, _ in rows) else 1

    status, criteria = record(path, work)
    print(f"G6: {status} ({EVIDENCE})")
    for row in criteria:
        print(f"  {row['id']:<18} {row['result']:<8} {row['description']}")
        print(f"          observed: {row['evidence_ref']}")
    return 0 if status == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
