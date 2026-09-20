#!/bin/sh
# Gate G0: environment, exact pin, one-time global sandbox configuration (tasks.md T008).
#
# POSIX sh. Every sbx surface it uses is documented in the Docker Sandboxes CLI reference
# (docs.docker.com/reference/cli/sbx) and verified against the installed CLI:
#   sbx version --json          client/server versions
#   sbx diagnose --json         installation, daemon and authentication checks
#   sbx ls --json               sandboxes
#   sbx settings get --json KEY / sbx settings set KEY VALUE   ssh.agentForwardingEnabled
#   sbx daemon restart
#   sbx policy ls --json        global network policy and governance
#   sbx policy init deny-all    bootstrap preset, only when the policy is uninitialized
# Nothing is inferred from table output, and no undocumented command is probed.
#
# Fail-closed sequencing: gates/G0/record.py re-checks the observations at each guard, so the
# gate runs strictly as preconditions -> SSH mutation -> proof that the SSH mutation took
# effect -> policy bootstrap. A failure at any guard stops the run before the next global
# change. G0 changes exactly two global things:
#   1. ssh.agentForwardingEnabled=false, followed by a daemon restart (R14, T008);
#   2. the global network policy preset, and only when sbx reports it uninitialized, to the
#      conservative deny-all. sbx requires a preset before the first sandbox runs
#      (docs.docker.com/ai/sandboxes/security/policy/, "Non-interactive environments"), and G6
#      creates sandboxes before G4 runs. An existing preset or governance state is recorded and
#      never overwritten. G4 stays authoritative for the proven effective policy.
#
# Raw command output stays in gates/G0/work/ (git-ignored). record.py parses it, writes
# gates/G0.json and, on PASS only, the two pins G0 owns in runtime/versions.yaml.

set -u

ROOT=$(CDPATH='' cd -- "$(dirname -- "$0")/../.." && pwd) || exit 1
cd "$ROOT" || exit 1

WORK=gates/G0/work
OBS=$WORK/observations.env
mkdir -p "$WORK" || exit 1
: >"$OBS"

record() {
    printf '%s=%s\n' "$1" "$2" >>"$OBS"
}

stop() {
    record stopped_before_global_change true
    python3 gates/G0/record.py "$OBS" "$WORK"
    exit 1
}

# --- step 1: Docker server, sbx, version, authentication ----------------------------------

server=$(docker version --format '{{if .Server}}{{.Server.Version}}{{end}}' 2>/dev/null)
record docker_server "$server"

if command -v sbx >/dev/null 2>&1; then
    record sbx_present true
    sbx version --json >"$WORK/version.json" 2>"$WORK/version.err"
    record version_exit "$?"
    # diagnose exits non-zero when any check fails; record.py reads the checks, not the exit code.
    sbx diagnose --json >"$WORK/diagnose.json" 2>"$WORK/diagnose.err"
    record diagnose_exit "$?"
else
    record sbx_present false
fi

# Read-only version observations, recorded whatever the outcome.
record docker_agent "$(docker agent version 2>/dev/null | head -n 1 | awk '{print $NF}')"
record claude_code "$(claude --version 2>/dev/null | awk '{print $1}')"
record git "$(git --version 2>/dev/null | awk '{print $3}')"

python3 gates/G0/record.py --step1 "$OBS" "$WORK" || stop

# --- step 2: one-time global SSH configuration (the first of G0's two global changes) ------

sbx ls --json >"$WORK/ls.json" 2>"$WORK/ls.err"
record ls_exit "$?"

python3 gates/G0/record.py --ready "$OBS" "$WORK" || stop

sbx settings get --json ssh.agentForwardingEnabled >"$WORK/settings-before.json" 2>"$WORK/settings-before.err"
record settings_before_exit "$?"
sbx settings set ssh.agentForwardingEnabled false >"$WORK/settings-set.txt" 2>&1
record settings_set_exit "$?"
sbx daemon restart >"$WORK/daemon-restart.txt" 2>&1
record daemon_restart_exit "$?"
sbx settings get --json ssh.agentForwardingEnabled >"$WORK/settings-after.json" 2>"$WORK/settings-after.err"
record settings_after_exit "$?"

# The stored value alone doesn't prove the change took effect: the setting is marked
# requires_restart, so the daemon must be healthy again after the restart.
sbx diagnose --json >"$WORK/diagnose-after.json" 2>"$WORK/diagnose-after.err"
record diagnose_after_exit "$?"

# The SSH change must be proven effective before the second global change is even considered:
# once it has failed, this run can't pass, so no further global state is touched.
python3 gates/G0/record.py --post-ssh "$OBS" "$WORK" || stop

# --- step 3: global network policy and governance -------------------------------------------

# Read-only. Its stdout, stderr and exit code together are what record.py matches exactly
# before any bootstrap: only the observed uninitialized representation authorizes policy init.
sbx policy ls --json >"$WORK/policy-before.json" 2>"$WORK/policy-before.err"
record policy_before_exit "$?"

if python3 gates/G0/record.py --policy-uninitialized "$OBS" "$WORK"; then
    # Uninitialized only: the conservative bootstrap preset, never allow-all, never balanced.
    sbx policy init deny-all >"$WORK/policy-init.txt" 2>&1
    record policy_init_exit "$?"
    record policy_init_preset deny-all
    record policy_previous_state uninitialized
else
    record policy_previous_state initialized
fi

sbx policy ls --json >"$WORK/policy-after.json" 2>"$WORK/policy-after.err"
record policy_after_exit "$?"

# --- step 4: evidence, and the pins G0 owns on PASS ----------------------------------------

python3 gates/G0/record.py "$OBS" "$WORK"
