#!/bin/sh
# Control-plane host inventory (tasks.md T014). Gate preparation, not a gate: it builds the draft
# inventory that lets G4 (T015) tell a documented or provider host apart from a host the sandboxed
# backend process actually requires at runtime. Verification only: nothing global changes.
#
# POSIX sh. Every sbx call goes through run_sbx, which removes SSH_AUTH_SOCK from the environment
# first (research R14). Only read-only sbx surfaces are used, plus create/rm for the kit-only
# sandboxes: sbx version/settings/policy/ls (read-only), sbx create --skills off, sbx policy ls
# <SANDBOX>, sbx rm --force. `sbx policy allow/deny/rm`, `sbx policy init`, `sbx reset` and
# `sbx rm --all` are never called, and the global preset and SSH settings are never touched.
#
# Sources, in the priority order T014 fixes:
#   1. kit    - the sandbox-scoped network rules a built-in agent kit adds, read from a kit-only
#               sandbox that holds NO repository code and runs no backend prompt.
#   2. docs   - official provider documentation, and the pinned docker-agent artifact V1 ships,
#               which is stronger evidence about Codex's endpoints than a note about it.
#   3. discovery - NOT used: sources 1 and 2 establish a runtime-control-plane host for both
#               backends, and T014 forbids running discovery merely for extra confidence.
#
# Note the CLI shape: `sbx policy ls [SANDBOX]` takes the sandbox positionally on the pinned
# version (there is no --sandbox flag on `ls`), so that is what this script uses.
#
# The two sandboxes are created and removed ONE AT A TIME. Only one microVM exists at any moment,
# which keeps the peak disk cost to a single VM; an earlier concurrent attempt exhausted the host
# volume mid-run. Each is mountless, explicitly named and removed by name, including on failure
# and before the evidence is written.

set -u

ROOT=$(CDPATH='' cd -- "$(dirname -- "$0")/../.." && pwd) || exit 1
cd "$ROOT" || exit 1

WORK=gates/inventory/work
OBS=$WORK/observations.env
KIT=gates/G6/work/kit
ARTIFACT=$KIT/files/home/dca-kit/docker-agent
SANDBOXES="dca-inv-claude dca-inv-codex"
CODEX_RUNTIME_URL=https://chatgpt.com/backend-api/codex
CODEX_LOGIN_URL=https://auth.openai.com

mkdir -p "$WORK" || exit 1
: >"$OBS"

record() {
    printf '%s=%s\n' "$1" "$2" >>"$OBS"
}

run_sbx() {
    env -u SSH_AUTH_SOCK sbx "$@"
}

sha256_of() {
    if command -v shasum >/dev/null 2>&1; then
        shasum -a 256 "$1" | awk '{print $1}'
    elif command -v sha256sum >/dev/null 2>&1; then
        sha256sum "$1" | awk '{print $1}'
    fi
}

# Belt and braces: the loop removes each sandbox as soon as it has been read, so this normally
# finds nothing left to remove. Its exits are deliberately NOT recorded as the cleanup evidence,
# because removing an already-removed sandbox fails and that is not a cleanup failure; the
# criterion reads the loop's own removals plus the final listing.
cleanup() {
    for name in $SANDBOXES; do
        run_sbx rm --force "$name" >"$WORK/sweep-$name.txt" 2>&1
    done
    run_sbx ls --json >"$WORK/ls-after.json" 2>&1
    record ls_after_exit "$?"
    run_sbx policy ls --json >"$WORK/policy-after.json" 2>"$WORK/policy-after.err"
    record policy_after_exit "$?"
}
trap cleanup EXIT INT TERM

stop() {
    record stopped_before_sandbox true
    cleanup
    trap - EXIT INT TERM
    python3 gates/inventory/record.py "$OBS" "$WORK"
    exit 1
}

# --- read-only preflight: the post-G0 state and the pinned identities --------------------------

record sbx_env_ssh_auth_sock "$(env -u SSH_AUTH_SOCK sh -c 'if [ -n "${SSH_AUTH_SOCK:-}" ]; then echo present; else echo removed; fi')"
record disk_free_kb_before "$(df -k . | awk 'NR==2 {print $4}')"

run_sbx version --json >"$WORK/pf-version.json" 2>"$WORK/pf-version.err"
record pf_version_exit "$?"
run_sbx settings get --json ssh.agentForwardingEnabled >"$WORK/pf-ssh-forwarding.json" 2>"$WORK/pf-ssh-forwarding.err"
record pf_ssh_forwarding_exit "$?"
run_sbx settings get --json ssh.agentSocketPath >"$WORK/pf-ssh-socket.json" 2>"$WORK/pf-ssh-socket.err"
record pf_ssh_socket_exit "$?"
run_sbx policy ls --json >"$WORK/pf-policy.json" 2>"$WORK/pf-policy.err"
record pf_policy_exit "$?"
run_sbx ls --json >"$WORK/pf-ls.json" 2>"$WORK/pf-ls.err"
record pf_ls_exit "$?"

# The G6 probe kit and, inside it, the pinned docker-agent artifact. The artifact is the Codex
# evidence: the exact binary V1 ships either contains the backend endpoint or it does not.
if [ -f "$ARTIFACT" ]; then
    record kit_present yes
    record artifact_sha256 "$(sha256_of "$ARTIFACT")"
    if LC_ALL=C grep -a -q -F "$CODEX_RUNTIME_URL" "$ARTIFACT"; then
        record artifact_runtime_url yes
    else
        record artifact_runtime_url no
    fi
    if LC_ALL=C grep -a -q -F "$CODEX_LOGIN_URL" "$ARTIFACT"; then
        record artifact_login_url yes
    else
        record artifact_login_url no
    fi
else
    record kit_present no
fi

python3 gates/inventory/record.py --preflight "$OBS" "$WORK" || stop

# --- source 1: what each built-in agent kit declares --------------------------------------------
#
# A kit-only sandbox: no workspace (mountless), no shared skills, no repository content, and no
# backend prompt is ever run in it. It exists only so the kit's own network declarations become
# readable as effective sandbox-scoped rules.

for pair in "claude dca-inv-claude claude" "codex dca-inv-codex docker-agent"; do
    backend=${pair%% *}
    rest=${pair#* }
    name=${rest%% *}
    agent=${rest#* }

    if [ "$backend" = codex ]; then
        run_sbx create "$agent" --name "$name" --skills off --kit "./$KIT" \
            >"$WORK/create-$name.txt" 2>&1
    else
        run_sbx create "$agent" --name "$name" --skills off >"$WORK/create-$name.txt" 2>&1
    fi
    record "create_${name}_exit" "$?"
    record "image_${name}" "$(awk '/^ *image  */ {print $2; exit}' "$WORK/create-$name.txt")"
    record "workspace_line_${name}" "$(awk '/^ *workspace  */ {$1=""; print; exit}' "$WORK/create-$name.txt" | sed 's/^ *//')"

    run_sbx policy ls "$name" --json >"$WORK/policy-$backend.json" 2>"$WORK/policy-$backend.err"
    record "policy_${name}_exit" "$?"
    run_sbx policy ls "$name" --wide >"$WORK/policy-$backend-wide.txt" 2>&1
    record "policy_wide_${name}_exit" "$?"

    # Removed immediately, so the next sandbox is created against a host that is not carrying this
    # one's disk image as well.
    run_sbx rm --force "$name" >"$WORK/rm-$name.txt" 2>&1
    record "rm_${name}_exit" "$?"
done

record disk_free_kb_after "$(df -k . | awk 'NR==2 {print $4}')"

cleanup
trap - EXIT INT TERM

python3 gates/inventory/record.py "$OBS" "$WORK"
