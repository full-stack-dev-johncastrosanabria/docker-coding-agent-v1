#!/bin/sh
# Gate G6: kit mechanics, Python version and artifact pin (tasks.md T009).
#
# POSIX sh. Every sbx surface is documented (docs.docker.com/reference/cli/sbx) and verified
# against the installed CLI: sbx version/settings/policy (read-only preflight), sbx kit validate,
# sbx create --skills off --kit, sbx exec, sbx ls --json, sbx rm --force.
#
# Pre-G6 global-state recheck (read-only): before the first sandbox is created, the global state
# G0 established must still hold — client and server pinned exact version and running,
# ssh.agentForwardingEnabled false, and the global network policy still in its post-G0 state.
# On drift the gate stops without creating a sandbox and without repairing anything.
#
# Both sandboxes are mountless (no PATH argument), created with --skills off, explicitly named,
# and removed by name at the end, including on failure. `sbx rm --all` is never used, and no
# unrelated sandbox, volume, credential or policy is touched.
#
# The kit stages the pinned docker-agent artifact from the host, so it declares no network
# permission and the sandbox needs no egress under the G0 deny-all bootstrap policy.

set -u

ROOT=$(CDPATH='' cd -- "$(dirname -- "$0")/../.." && pwd) || exit 1
cd "$ROOT" || exit 1

WORK=gates/G6/work
OBS=$WORK/observations.env
KIT=$WORK/kit
ARTIFACT=$WORK/docker-agent-linux-arm64
ARTIFACT_URL=https://github.com/docker/docker-agent/releases/download/v1.136.0/docker-agent-linux-arm64
CLAUDE_SANDBOX=dca-g6-claude
CODEX_SANDBOX=dca-g6-codex

mkdir -p "$WORK" || exit 1
: >"$OBS"

record() {
    printf '%s=%s\n' "$1" "$2" >>"$OBS"
}

# Always remove exactly the two named probe sandboxes, whatever happened.
cleanup() {
    for name in "$CLAUDE_SANDBOX" "$CODEX_SANDBOX"; do
        sbx rm --force "$name" >"$WORK/rm-$name.txt" 2>&1
        record "rm_${name}_exit" "$?"
    done
    sbx ls --json >"$WORK/ls-after.json" 2>&1
    record ls_after_exit "$?"
}
trap cleanup EXIT INT TERM

# A failed run cleans up first, so the rm exit codes and the final listing are part of the
# FAIL evidence rather than happening after it was written.
stop() {
    record stopped_before_sandbox true
    cleanup
    trap - EXIT INT TERM
    python3 gates/G6/record.py "$OBS" "$WORK"
    exit 1
}

# --- pre-G6 global-state recheck (read-only) ------------------------------------------------

sbx version --json >"$WORK/pf-version.json" 2>"$WORK/pf-version.err"
record pf_version_exit "$?"
sbx settings get --json ssh.agentForwardingEnabled >"$WORK/pf-ssh.json" 2>"$WORK/pf-ssh.err"
record pf_ssh_exit "$?"
sbx policy ls --json >"$WORK/pf-policy.json" 2>"$WORK/pf-policy.err"
record pf_policy_exit "$?"
sbx ls --json >"$WORK/pf-ls.json" 2>"$WORK/pf-ls.err"
record pf_ls_exit "$?"

python3 gates/G6/record.py --preflight "$OBS" "$WORK" || stop

# --- pinned artifact (fetched on the host; the sandbox needs no egress) ---------------------

if [ ! -f "$ARTIFACT" ]; then
    gh release download v1.136.0 --repo docker/docker-agent \
        --pattern 'docker-agent-linux-arm64' --dir "$WORK" >"$WORK/download.txt" 2>&1
    record artifact_download_exit "$?"
fi
# Authoritative release metadata: the official GitHub Releases API publishes the asset's size
# and digest, which record.py compares with the downloaded bytes and the locally computed hash.
gh api repos/docker/docker-agent/releases/tags/v1.136.0 >"$WORK/release.json" 2>"$WORK/release.err"
record release_metadata_exit "$?"

record artifact_url "$ARTIFACT_URL"
record artifact_sha256 "$(shasum -a 256 "$ARTIFACT" 2>/dev/null | awk '{print $1}')"
record artifact_bytes "$(wc -c <"$ARTIFACT" 2>/dev/null | tr -d ' ')"
# The release publishes no checksum file and GitHub holds no attestation for this artifact
# (recorded in work/publisher-checksum.txt), so the pin is a reproducibility pin, never a
# publisher-signature claim.
gh attestation verify "$ARTIFACT" --repo docker/docker-agent >"$WORK/publisher-checksum.txt" 2>&1
record artifact_attestation_exit "$?"

# Read-only view of the local sandbox runtime image store, which ties each base to the image
# sbx actually cached. Never `sbx reset`, and the cache is never deleted.
sbx template ls --json >"$WORK/templates.json" 2>"$WORK/templates.err"
record template_ls_exit "$?"

# --- stage the kit ---------------------------------------------------------------------------

rm -rf "$KIT"
cp -R gates/G6/kit "$KIT" || exit 1
cp "$ARTIFACT" "$KIT/files/home/dca-kit/docker-agent" || exit 1
sbx kit validate "$KIT" >"$WORK/kit-validate.txt" 2>&1
record kit_validate_exit "$?"

# --- one sandbox per base --------------------------------------------------------------------

probe() {
    # $1 = sandbox name. Reads only paths the kit installed; prints key=value lines.
    sbx exec -u root "$1" sh -c '
        printf "docker_agent_version=%s\n" "$(/opt/dca/bin/docker-agent version 2>/dev/null | head -n 1 | awk "{print \$NF}")"
        printf "docker_agent_sha256=%s\n" "$(sha256sum /opt/dca/bin/docker-agent 2>/dev/null | awk "{print \$1}")"
        printf "python3_path=%s\n" "$(test -x /usr/bin/python3 && echo /usr/bin/python3 || echo none)"
        /usr/bin/python3 -c "import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)" 2>/dev/null
        printf "python3_min_exit=%s\n" "$?"
        printf "python3_version=%s\n" "$(/usr/bin/python3 -V 2>&1 | awk "{print \$2}")"
        printf "kit_probe=%s\n" "$(test -f /opt/dca/kit-probe.txt && echo yes || echo no)"
        printf "kit_manifest=%s\n" "$(test -f /opt/dca/kit-manifest.json && echo yes || echo no)"
        printf "managed_settings=%s\n" "$(test -f /etc/claude-code/managed-settings.json && echo yes || echo no)"
        printf "skill_one=%s\n" "$(test -f /opt/dca/skills/dca-probe-one/SKILL.md && echo yes || echo no)"
        printf "skill_two=%s\n" "$(test -f /opt/dca/skills/dca-probe-two/SKILL.md && echo yes || echo no)"
        printf "skills_store_mounted=%s\n" "$(awk "\$2 == \"/home/agent/.claude/skills\" {c++} END {print c+0}" /proc/mounts)"
        printf "workspace_mounted=%s\n" "$(awk "\$2 == \"/home/agent/workspace\" {c++} END {print c+0}" /proc/mounts)"
        printf "host_fs_mount_targets=%s\n" "$(awk "\$3 ~ /^(virtiofs|9p|nfs)\$/ {printf \"%s \", \$2}" /proc/mounts)"
        printf "workspace_entries=%s\n" "$(ls -A /home/agent/workspace 2>/dev/null | wc -l | tr -d " ")"
        printf "os_release=%s\n" "$(. /etc/os-release 2>/dev/null; echo "${PRETTY_NAME:-unknown}")"
    '
}

for pair in "claude $CLAUDE_SANDBOX" "docker-agent $CODEX_SANDBOX"; do
    agent=${pair% *}
    name=${pair#* }

    # Mountless: no PATH argument. Shared skills store off. Explicit name.
    sbx create "$agent" --name "$name" --skills off --kit "./$KIT" \
        >"$WORK/create-$name.txt" 2>&1
    record "create_${name}_exit" "$?"

    sbx ls --json >"$WORK/ls-$name.json" 2>&1
    record "ls_${name}_exit" "$?"

    # The base sbx resolved, from its own RESOLVE SETUP block, plus the registry digest that
    # tag pointed at when the gate ran: the tag alone is not an exact version.
    image=$(awk '/^ *image  */ {print $2; exit}' "$WORK/create-$name.txt")
    record "image_${name}" "$image"
    record "workspace_line_${name}" "$(awk '/^ *workspace  */ {$1=""; print; exit}' "$WORK/create-$name.txt" | sed 's/^ *//')"
    repo=${image%%:*}
    tag=${image##*:}
    token=$(curl -fsS "https://auth.docker.io/token?service=registry.docker.io&scope=repository:${repo}:pull" 2>/dev/null |
        python3 -c 'import json,sys; print(json.load(sys.stdin)["token"])' 2>/dev/null)
    record "base_digest_${name}" "$(curl -fsSI -H "Authorization: Bearer $token" \
        -H 'Accept: application/vnd.oci.image.index.v1+json,application/vnd.docker.distribution.manifest.list.v2+json,application/vnd.docker.distribution.manifest.v2+json' \
        "https://registry-1.docker.io/v2/${repo}/manifests/${tag}" 2>/dev/null |
        tr -d '\r' | awk -F': ' 'tolower($1)=="docker-content-digest"{print $2}')"

    probe "$name" >"$WORK/probe-$name.env" 2>"$WORK/probe-$name.err"
    record "probe_${name}_exit" "$?"
done

# --- evidence and the pins G6 owns -------------------------------------------------------------

cleanup
trap - EXIT INT TERM

python3 gates/G6/record.py "$OBS" "$WORK"
