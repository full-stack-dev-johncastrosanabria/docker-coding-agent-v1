"""Evaluate the G6 observations and write gates/G6.json (tasks.md T009).

Standard library only. It reads what gates/G6/run.sh captured and decides every criterion from
machine-readable output plus exit status. Shapes come from the sbx pinned by G0 (v0.53.0, v3
kits); they are empirically observed, not a promised schema, and an sbx upgrade re-runs the gates
(R27).

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
sbx_module = _load_module("dca_sbx", os.path.join(ROOT, "src", "dca", "sbx.py"))

with open(VERSIONS, encoding="utf-8") as _pins_file:
    PINS = json.load(_pins_file)
DOCKER_AGENT_VERSION = PINS["docker_agent"]
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
KIT = os.path.join(WORK, "kit")
KIT_RECIPE = "FROM scratch;COPY payload/ /;"


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
    try:
        return sbx_module.sandbox_inventory(listing)
    except (ValueError, TypeError, KeyError):
        return None


def sandbox_entry(work, name, sandbox_name):
    entries = sandboxes(read_json(work, name))
    for entry in entries or []:
        if isinstance(entry, dict) and entry.get("name") == sandbox_name:
            return entry
    return None


def base_identifier(obs, sandbox, work=None):
    """(workload repository, registry digest at gate time, sbx inspect record) for a sandbox.

    The sandbox is created from `<repository>@<digest>`, and `sbx inspect --json` is sbx's own
    record of the kits it composed - the proof that THAT digest booted.
    """
    image = obs.get(f"image_{sandbox}") or None
    digest = obs.get(f"base_digest_{sandbox}") or None
    if digest and not (digest.startswith("sha256:") and len(digest) == 71):
        digest = None
    record = read_json(work, f"inspect-{sandbox}.json") if work else None
    return image, digest, record


def release_asset(work):
    """The docker-agent release asset from the official GitHub release metadata, or None."""
    release = read_json(work, "release.json")
    for asset in (release or {}).get("assets", []) or []:
        if isinstance(asset, dict) and asset.get("name") == ARTIFACT_ASSET:
            return asset
    return None


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
            "the probe kit is a v3 mixin from host-provided files only, built by the pinned builder",
            obs.get("kit_frontend") == "# syntax=" + PINS["kit_build"]["frontend"]
            and obs.get("kit_network_capabilities") == "0"
            and obs.get("kit_recipe") == KIT_RECIPE
            and obs.get("builder_inspect_exit") == "0"
            and obs.get("builder_image") == PINS["kit_build"]["buildkit_image"],
            f"frontend line {obs.get('kit_frontend', '-')!r}; network-policy capabilities "
            f"{obs.get('kit_network_capabilities', '-')}; recipe {obs.get('kit_recipe', '-')!r}; "
            f"buildx builder {PINS['kit_build']['builder']} inspect exit "
            f"{obs.get('builder_inspect_exit', '-')}, BuildKit image {obs.get('builder_image', '-')}",
        )
    )

    for backend, sandbox in BACKENDS:
        probe = read_env(work, f"probe-{sandbox}.env")
        entry = sandbox_entry(work, f"ls-{sandbox}.json", sandbox)
        base, base_version, inspected = base_identifier(obs, sandbox, work)
        mounts = (inspected or {}).get("host_mounts") if isinstance(inspected, dict) else None
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
                and mounts == []
                and probe.get("workspace_mounted") == "0"
                and probe.get("workspace_entries") == "0"
                and probe.get("skills_store_mounted") == "0",
                f"create exit {obs.get(f'create_{sandbox}_exit', '-')}, probe exit "
                f"{obs.get(f'probe_{sandbox}_exit', '-')}, sbx inspect host_mounts "
                f"{mounts!r}, mounts at the workspace path "
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
        pinned = f"{base}@{base_version}" if base and base_version else None
        problem = (sbx_module.composition_problem(inspected, pinned, KIT) if pinned
                   else "no workload digest was resolved")
        rows.append(
            (
                f"G6.{backend}.base",
                f"{backend}: the sandbox is exactly the workload kit at the gate-time digest plus the probe kit",
                obs.get(f"inspect_{sandbox}_exit") == "0" and problem is None,
                f"registry digest of {base or '-'}:latest at gate time {base_version or 'not resolved'}; "
                f"sbx inspect kits {(inspected or {}).get('kits') if isinstance(inspected, dict) else None}; "
                f"{'composition proven' if problem is None else problem}",
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
