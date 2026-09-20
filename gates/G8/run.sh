#!/bin/sh
# Gate G8: shared-skills isolation (tasks.md T011). Verification only: nothing global changes.
#
# POSIX sh. Every sbx call goes through run_sbx, which removes SSH_AUTH_SOCK from the
# environment first (research R14), and every surface used is documented and verified against
# the installed CLI: sbx version/settings/policy/template/skills (read-only), sbx create
# --skills off|readonly --kit, sbx exec, sbx ls --json, sbx rm --force.
#
# What it proves, at the current probe stage: a sandbox created with --skills=off has no shared
# skills-store mount, no host or shared skill appears in it, and with the G6 probe kit the skill
# directories hold exactly that kit's two probe skills. It does NOT prove the final four runtime
# skills: those arrive with T047-T050 and the production kit (T056), and are proven by
# production conformance (T062).
#
# Both pinned bases are covered, because the shared store is mounted at "the agent's skills
# directory" (sbx create --help), which is agent-specific, so the observed shape may differ.
#
# For each base a second, short-lived control sandbox is created with --skills readonly. It
# shows whether a shared-store mount is detectable at all on this host, so the --skills=off
# observation is a differential rather than a bare negative.
#
# Sandboxes are mountless, explicitly named and removed by name at the end, including on
# failure and before the evidence is written. `sbx rm --all` and `sbx reset` are never used.

set -u

ROOT=$(CDPATH='' cd -- "$(dirname -- "$0")/../.." && pwd) || exit 1
cd "$ROOT" || exit 1

WORK=gates/G8/work
OBS=$WORK/observations.env
KIT=gates/G6/work/kit
SANDBOXES="dca-g8-claude dca-g8-codex dca-g8-claude-control dca-g8-codex-control"

mkdir -p "$WORK" || exit 1
: >"$OBS"

record() {
    printf '%s=%s\n' "$1" "$2" >>"$OBS"
}

run_sbx() {
    env -u SSH_AUTH_SOCK sbx "$@"
}

cleanup() {
    for name in $SANDBOXES; do
        run_sbx rm --force "$name" >"$WORK/rm-$name.txt" 2>&1
        record "rm_${name}_exit" "$?"
    done
    run_sbx ls --json >"$WORK/ls-after.json" 2>&1
    record ls_after_exit "$?"
}
trap cleanup EXIT INT TERM

stop() {
    record stopped_before_sandbox true
    cleanup
    trap - EXIT INT TERM
    python3 gates/G8/record.py "$OBS" "$WORK"
    exit 1
}

# --- read-only preflight: the post-G0 state, the SSH detector and the pinned identities -------

record sbx_env_ssh_auth_sock "$(env -u SSH_AUTH_SOCK sh -c 'if [ -n "${SSH_AUTH_SOCK:-}" ]; then echo present; else echo removed; fi')"

run_sbx version --json >"$WORK/pf-version.json" 2>"$WORK/pf-version.err"
record pf_version_exit "$?"
run_sbx settings get --json ssh.agentForwardingEnabled >"$WORK/pf-ssh-forwarding.json" 2>"$WORK/pf-ssh-forwarding.err"
record pf_ssh_forwarding_exit "$?"
run_sbx settings get --json ssh.agentSocketPath >"$WORK/pf-ssh-socket.json" 2>"$WORK/pf-ssh-socket.err"
record pf_ssh_socket_exit "$?"
run_sbx policy ls --json >"$WORK/pf-policy.json" 2>"$WORK/pf-policy.err"
record pf_policy_exit "$?"
run_sbx template ls --json >"$WORK/templates.json" 2>"$WORK/templates.err"
record template_ls_exit "$?"
# The host's shared skills store, read-only: its contents decide how much a bare "nothing was
# injected" observation is worth, which the control sandboxes then test directly.
run_sbx skills ls --json >"$WORK/host-skills.json" 2>"$WORK/host-skills.err"
record host_skills_exit "$?"
run_sbx ls --json >"$WORK/pf-ls.json" 2>"$WORK/pf-ls.err"
record pf_ls_exit "$?"

python3 gates/G8/record.py --preflight "$OBS" "$WORK" || stop

[ -d "$KIT" ] || { record kit_missing true; stop; }
record kit_path "$KIT"

# --- the probe ---------------------------------------------------------------------------------

probe() {
    # $1 = sandbox name. Discovers skill directories rather than assuming paths, and records
    # only names, counts and paths.
    run_sbx exec "$1" sh -c '
        printf "mount_total=%s\n" "$(wc -l < /proc/mounts | tr -d " ")"
        printf "mount_skill_targets=%s\n" "$(awk "\$2 ~ /[Ss]kill/ {printf \"%s \", \$2}" /proc/mounts)"
        dirs=$(find / -maxdepth 6 -type d -name "skills" -not -path "/proc/*" -not -path "/sys/*" -not -path "/dev/*" 2>/dev/null | sort)
        printf "skills_dirs=%s\n" "$(printf "%s" "$dirs" | tr "\n" " ")"
        i=0
        for d in $dirs; do
            i=$((i + 1))
            printf "skills_dir_%s=%s\n" "$i" "$d"
            printf "skills_dir_%s_entries=%s\n" "$i" "$(ls -A "$d" 2>/dev/null | tr "\n" " ")"
            printf "skills_dir_%s_mounted=%s\n" "$i" "$(awk -v p="$d" "\$2 == p {c++} END {print c+0}" /proc/mounts)"
        done
        printf "skills_dir_count=%s\n" "$i"
        printf "kit_skills_entries=%s\n" "$(ls -A /opt/dca/skills 2>/dev/null | tr "\n" " ")"
        printf "speckit_hits=%s\n" "$(find / -maxdepth 8 -name "speckit-*" -not -path "/proc/*" -not -path "/sys/*" -not -path "/dev/*" 2>/dev/null | wc -l | tr -d " ")"
        printf "probe_complete=yes\n"
    '
}

# --- one subject and one control per pinned base -------------------------------------------------

for pair in "claude dca-g8-claude" "docker-agent dca-g8-codex"; do
    agent=${pair% *}
    name=${pair#* }

    # Subject: mountless, --skills off, the G6 probe kit.
    run_sbx create "$agent" --name "$name" --skills off --kit "./$KIT" >"$WORK/create-$name.txt" 2>&1
    record "create_${name}_exit" "$?"
    record "image_${name}" "$(awk '/^ *image  */ {print $2; exit}' "$WORK/create-$name.txt")"
    record "workspace_line_${name}" "$(awk '/^ *workspace  */ {$1=""; print; exit}' "$WORK/create-$name.txt" | sed 's/^ *//')"
    probe "$name" >"$WORK/probe-$name.env" 2>"$WORK/probe-$name.err"
    record "probe_${name}_exit" "$?"

    # Control: same base, mountless, shared store left at readonly, no kit. It answers whether a
    # shared-store mount is observable at all here.
    control="$name-control"
    run_sbx create "$agent" --name "$control" --skills readonly >"$WORK/create-$control.txt" 2>&1
    record "create_${control}_exit" "$?"
    probe "$control" >"$WORK/probe-$control.env" 2>"$WORK/probe-$control.err"
    record "probe_${control}_exit" "$?"
done

cleanup
trap - EXIT INT TERM

python3 gates/G8/record.py "$OBS" "$WORK"
