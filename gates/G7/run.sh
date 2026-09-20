#!/bin/sh
# Gate G7: SSH-agent isolation, verification only (tasks.md T010). G0 already disabled
# forwarding; this gate proves no agent is reachable inside a sandbox and that the read-only
# detector refuses both states in which one would be.
#
# POSIX sh. Every sbx call goes through run_sbx, which removes SSH_AUTH_SOCK from the
# environment first, exactly as the launcher must (research R14). Nothing global is changed:
# the negative cases come from recorded documents under gates/G7/recorded/, never from
# switching a setting on this host.
#
# The sandbox is mountless (no workspace path), created with --skills off, explicitly named,
# and removed by name at the end, including on failure, before the evidence is written.
# `sbx rm --all` is never used.

set -u

ROOT=$(CDPATH='' cd -- "$(dirname -- "$0")/../.." && pwd) || exit 1
cd "$ROOT" || exit 1

WORK=gates/G7/work
OBS=$WORK/observations.env
SANDBOX=dca-g7-ssh

mkdir -p "$WORK" || exit 1
: >"$OBS"

record() {
    printf '%s=%s\n' "$1" "$2" >>"$OBS"
}

# Every sbx invocation runs without the host's SSH agent socket in its environment.
run_sbx() {
    env -u SSH_AUTH_SOCK sbx "$@"
}

cleanup() {
    run_sbx rm --force "$SANDBOX" >"$WORK/rm-$SANDBOX.txt" 2>&1
    record "rm_${SANDBOX}_exit" "$?"
    run_sbx ls --json >"$WORK/ls-after.json" 2>&1
    record ls_after_exit "$?"
}
trap cleanup EXIT INT TERM

stop() {
    record stopped_before_sandbox true
    cleanup
    trap - EXIT INT TERM
    python3 gates/G7/record.py "$OBS" "$WORK"
    exit 1
}

# --- host-side facts about the agent socket we remove ----------------------------------------

record host_ssh_auth_sock_set "$(if [ -n "${SSH_AUTH_SOCK:-}" ]; then echo yes; else echo no; fi)"
record sbx_env_ssh_auth_sock "$(env -u SSH_AUTH_SOCK sh -c 'if [ -n "${SSH_AUTH_SOCK:-}" ]; then echo present; else echo removed; fi')"

# --- read-only global-state recheck and the SSH detector -------------------------------------

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

run_sbx template ls --json >"$WORK/templates.json" 2>"$WORK/templates.err"
record template_ls_exit "$?"

python3 gates/G7/record.py --preflight "$OBS" "$WORK" || stop

# --- the sandbox ------------------------------------------------------------------------------

run_sbx create claude --name "$SANDBOX" --skills off >"$WORK/create-$SANDBOX.txt" 2>&1
record "create_${SANDBOX}_exit" "$?"
record "image_${SANDBOX}" "$(awk '/^ *image  */ {print $2; exit}' "$WORK/create-$SANDBOX.txt")"
record "workspace_line_${SANDBOX}" "$(awk '/^ *workspace  */ {$1=""; print; exit}' "$WORK/create-$SANDBOX.txt" | sed 's/^ *//')"

# Probe as the agent user: this is what a workload would see. It records only booleans, exit
# codes, counts and paths, never key material.
run_sbx exec "$SANDBOX" sh -c '
    printf "ssh_auth_sock=%s\n" "$(if [ -n "${SSH_AUTH_SOCK:-}" ]; then echo "set"; else echo "unset"; fi)"
    printf "ssh_auth_sock_path=%s\n" "${SSH_AUTH_SOCK:-}"
    printf "ssh_auth_sock_exists=%s\n" "$(if [ -e "${SSH_AUTH_SOCK:-/nonexistent}" ]; then echo yes; else echo no; fi)"
    printf "ssh_auth_sock_is_socket=%s\n" "$(if [ -S "${SSH_AUTH_SOCK:-/nonexistent}" ]; then echo yes; else echo no; fi)"
    printf "ssh_auth_sock_dir_exists=%s\n" "$(d=$(dirname "${SSH_AUTH_SOCK:-/nonexistent}"); if [ -d "$d" ]; then echo yes; else echo no; fi)"
    printf "pid1_has_ssh_auth_sock=%s\n" "$(tr "\0" "\n" < /proc/1/environ 2>/dev/null | grep -c "^SSH_AUTH_SOCK=" || true)"
    printf "image_declares_ssh=%s\n" "$(grep -c "SSH_AUTH_SOCK" /etc/environment /etc/profile 2>/dev/null | awk -F: "{s+=\$2} END {print s+0}")"
    printf "ssh_agent_pid=%s\n" "$(if [ -n "${SSH_AGENT_PID:-}" ]; then echo "set"; else echo "unset"; fi)"
    printf "ssh_env_names=%s\n" "$(env | grep -c "^SSH_" || true)"
    printf "ssh_add_present=%s\n" "$(if command -v ssh-add >/dev/null 2>&1; then echo yes; else echo no; fi)"
    if command -v ssh-add >/dev/null 2>&1; then
        out=$(ssh-add -l 2>&1)
        printf "ssh_add_exit=%s\n" "$?"
        # Only an unreachable agent is safe. "The agent has no identities" means an agent
        # answered with zero keys loaded, which is a reachable agent.
        printf "ssh_add_cannot_connect=%s\n" "$(printf "%s" "$out" | grep -qiE "could not open a connection|error connecting" && echo yes || echo no)"
        printf "ssh_add_reports_identities=%s\n" "$(printf "%s" "$out" | grep -qiE "no identities|[0-9]+ [0-9a-fA-F:]+" && echo yes || echo no)"
    else
        printf "ssh_add_exit=absent\n"
        printf "ssh_add_cannot_connect=absent\n"
        printf "ssh_add_reports_identities=absent\n"
    fi
    printf "agent_sockets=%s\n" "$(find /tmp /run /var/run -maxdepth 3 -type s 2>/dev/null | grep -ciE "ssh|agent" || true)"
    printf "socket_paths=%s\n" "$(find /tmp /run /var/run -maxdepth 3 -type s 2>/dev/null | grep -iE "ssh|agent" | tr "\n" " ")"
' >"$WORK/probe-$SANDBOX.env" 2>"$WORK/probe-$SANDBOX.err"
record "probe_${SANDBOX}_exit" "$?"

# --- negative detector cases, from recorded documents only -------------------------------------
# No setting is changed: record.py runs the same detector over gates/G7/recorded/*.json.

record recorded_cases "forwarding-enabled fixed-socket"

cleanup
trap - EXIT INT TERM

python3 gates/G7/record.py "$OBS" "$WORK"
