#!/bin/sh
# Gate G9: control-plane capability non-usability (tasks.md T023, research R13).
#
# POSIX sh. Every sbx call goes through run_sbx, which removes SSH_AUTH_SOCK first (research R14).
#
# THE QUESTION. G1b and G2 asked whether a repository workload can READ credential material. G9 asks
# whether it can USE the agent's authenticated control-plane capability anyway - through a sentinel,
# a placeholder, a credential helper, a proxy injection, or simply by sending the client's own
# request shape. PASS requires proving it cannot, on every available backend.
#
# ORDER MATTERS. The positive control runs FIRST and its sbx policy log is captured BEFORE the
# workload sends anything, so the log entry that ties the control to the probe host is the agent's
# own traffic and not the workload's. Without that ordering the control could not be attributed.
#
# A 401 FROM THE WORKLOAD PROVES NOTHING ON ITS OWN. If the capability were simply unreachable from
# this sandbox, every variant would also return 401 and the gate would be vacuous. That is why each
# probe must first show the legitimate agent path producing the mediation signal on the SAME
# endpoint and request family, in the SAME sandbox.
#
# One sandbox at a time, mountless, --skills off, from each backend's exact pinned base, under the
# accepted G4 policy for that backend. Codex runs --safety strict and carries only the minimal
# chatgpt-auth.json the accepted G2 decision selected.

set -u

ROOT=$(CDPATH='' cd -- "$(dirname -- "$0")/../.." && pwd) || exit 1
cd "$ROOT" || exit 1

WORK=gates/G9/work
OBS=$WORK/observations.env
KIT=gates/G6/work/kit
GOVERNANCE_PROBE=example.invalid:443

AGENT_BIN=/opt/dca/bin/docker-agent
CREDENTIAL=chatgpt-auth.json
HOST_CONFIG_DIR=$HOME/.config/cagent
VM_CONFIG=/tmp/dca-g9-cagent
MARKER=DCA-G9-OK

record() {
    printf '%s=%s\n' "$1" "$2" >>"$OBS"
}

run_sbx() {
    env -u SSH_AUTH_SOCK sbx "$@"
}

cleanup() {
    for name in dca-g9-claude dca-g9-codex; do
        if [ -f "$WORK/created-$name" ]; then
            run_sbx rm --force "$name" >"$WORK/sweep-$name.txt" 2>&1
        fi
    done
    run_sbx ls --json >"$WORK/ls-after.json" 2>&1
    record ls_after_exit "$?"
    run_sbx policy ls --json >"$WORK/policy-after.json" 2>"$WORK/policy-after.err"
    record policy_after_exit "$?"
    run_sbx policy check network "$GOVERNANCE_PROBE" --json \
        >"$WORK/governance-after.json" 2>"$WORK/governance-after.err"
    record governance_after_exit "$?"
    [ -n "${CRED_DIR:-}" ] && rm -rf "$CRED_DIR"
}

stop() {
    record stopped_early true
    cleanup
    trap - EXIT INT TERM
    python3 gates/G9/record.py "$OBS" "$WORK"
    exit 1
}

case $WORK in
    gates/G9/work) rm -rf "$WORK" || exit 1 ;;
    *) echo "refusing to clear unexpected work dir: $WORK" >&2; exit 1 ;;
esac
mkdir -p "$WORK" || exit 1
: >"$OBS"

# --- read-only preflight, with zero sandboxes ------------------------------------------------------
mkdir -p "$WORK/envprobe" || exit 1
cat >"$WORK/envprobe/sbx" <<'STUB'
#!/bin/sh
if [ -n "${SSH_AUTH_SOCK:-}" ]; then echo present; else echo removed; fi
STUB
chmod +x "$WORK/envprobe/sbx" || exit 1
record sbx_env_ssh_auth_sock "$(
    PATH="$ROOT/$WORK/envprobe:$PATH"
    SSH_AUTH_SOCK=/nonexistent/g9-env-probe.sock
    export PATH SSH_AUTH_SOCK
    run_sbx env-probe 2>/dev/null
)"

run_sbx version --json >"$WORK/pf-version.json" 2>"$WORK/pf-version.err"
record pf_version_exit "$?"
run_sbx settings get --json ssh.agentForwardingEnabled \
    >"$WORK/pf-ssh-forwarding.json" 2>"$WORK/pf-ssh-forwarding.err"
record pf_ssh_forwarding_exit "$?"
run_sbx settings get --json ssh.agentSocketPath \
    >"$WORK/pf-ssh-socket.json" 2>"$WORK/pf-ssh-socket.err"
record pf_ssh_socket_exit "$?"
run_sbx policy ls --json >"$WORK/pf-policy.json" 2>"$WORK/pf-policy.err"
record pf_policy_exit "$?"
run_sbx ls --json >"$WORK/pf-ls.json" 2>"$WORK/pf-ls.err"
record pf_ls_exit "$?"
run_sbx policy check network "$GOVERNANCE_PROBE" --json \
    >"$WORK/governance-before.json" 2>"$WORK/governance-before.err"
record governance_before_exit "$?"

python3 gates/G9/record.py --preflight "$OBS" "$WORK" || stop

AVAILABLE=$(python3 gates/G9/record.py --available) || stop
record available_backends "$AVAILABLE"
[ -n "$AVAILABLE" ] || { record no_backend_available true; stop; }
[ -d "$KIT" ] || { record kit_missing true; stop; }

mkdir -p "$WORK/tasks" || exit 1
printf 'Reply with exactly the token %s and nothing else. Do not use any tool.\n' "$MARKER" \
    >"$WORK/tasks/trivial.txt"

CRED_DIR=""
trap 'cleanup' EXIT INT TERM

for backend in $AVAILABLE; do
    case $backend in
        claude)
            SANDBOX=dca-g9-claude
            TEMPLATE=claude
            AGENT=gates/G9/agent-claude.yaml
            SAFETY=""
            HOSTPROBE=api.anthropic.com
            ;;
        codex)
            SANDBOX=dca-g9-codex
            TEMPLATE=docker-agent
            AGENT=gates/G9/agent-codex.yaml
            SAFETY="--safety strict"
            HOSTPROBE=chatgpt.com
            ;;
        *) record "unknown_backend_$backend" true; stop ;;
    esac
    record "probe_host_$backend" "$HOSTPROBE"

    run_sbx ls --json >"$WORK/ls-before-$backend.json" 2>&1
    record "ls_before_${backend}_exit" "$?"

    run_sbx create "$TEMPLATE" --name "$SANDBOX" --skills off --kit "./$KIT" \
        >"$WORK/create-$SANDBOX.txt" 2>&1
    create_status=$?
    record "create_${SANDBOX}_exit" "$create_status"
    [ "$create_status" -eq 0 ] && : >"$WORK/created-$SANDBOX"
    [ "$create_status" -eq 0 ] || stop

    run_sbx template ls --json >"$WORK/templates.json" 2>"$WORK/templates.err"
    record templates_exit "$?"
    record "sbx_resolved_base_$backend" \
        "$(sed -n 's/.*\(docker\/sandbox-templates:[a-z0-9.-]*\).*/\1/p' \
           "$WORK/create-$SANDBOX.txt" | head -1)"

    POLICY_ALLOW=$(python3 gates/G9/record.py --policy "$backend" allow) || stop
    POLICY_DENY=$(python3 gates/G9/record.py --policy "$backend" deny) || stop
    record "policy_allow_$backend" "$POLICY_ALLOW"
    record "policy_deny_$backend" "$POLICY_DENY"

    rules_status=0
    run_sbx policy allow network --sandbox "$SANDBOX" "$POLICY_ALLOW" \
        >"$WORK/allow-$SANDBOX.txt" 2>&1 || rules_status=1
    if [ -n "$POLICY_DENY" ]; then
        run_sbx policy deny network --sandbox "$SANDBOX" "$POLICY_DENY" \
            >"$WORK/deny-$SANDBOX.txt" 2>&1 || rules_status=1
    fi
    record "policy_rules_${backend}_exit" "$rules_status"

    run_sbx cp "$AGENT" "$SANDBOX":/tmp/agent.yaml >"$WORK/cp-agent-$backend.txt" 2>&1
    record "cp_agent_${backend}_exit" "$?"
    run_sbx cp "$WORK/tasks" "$SANDBOX":/tmp/dca-g9-tasks >"$WORK/cp-tasks-$backend.txt" 2>&1
    record "cp_tasks_${backend}_exit" "$?"
    run_sbx cp gates/G9/probes.json "$SANDBOX":/tmp/dca-g9-probes.json \
        >"$WORK/cp-probes-$backend.txt" 2>&1
    record "cp_probes_${backend}_exit" "$?"
    run_sbx cp gates/G9/oracle.py "$SANDBOX":/tmp/dca-g9-oracle.py \
        >"$WORK/cp-oracle-$backend.txt" 2>&1
    record "cp_oracle_${backend}_exit" "$?"
    run_sbx cp gates/G9/workload.py "$SANDBOX":/tmp/dca-g9-workload.py \
        >"$WORK/cp-workload-$backend.txt" 2>&1
    record "cp_workload_${backend}_exit" "$?"
    run_sbx cp gates/G1b/scan.py "$SANDBOX":/tmp/dca-g1b-scan.py \
        >"$WORK/cp-scan1-$backend.txt" 2>&1
    record "cp_scan1_${backend}_exit" "$?"
    run_sbx cp gates/G2/scan.py "$SANDBOX":/tmp/dca-g2-scan.py \
        >"$WORK/cp-scan2-$backend.txt" 2>&1
    record "cp_scan2_${backend}_exit" "$?"
    # The exact safety flag the positive control carries, recorded so the evidence states it rather
    # than implying it. Empty for Claude, whose harness has no --safety flag.
    record "safety_flag_$backend" "$SAFETY"

    CONFIG_FLAG=""
    if [ "$backend" = codex ]; then
        [ -f "$HOST_CONFIG_DIR/$CREDENTIAL" ] || { record host_credential_missing true; stop; }
        CRED_DIR=$(mktemp -d) || stop
        chmod 700 "$CRED_DIR" || stop
        cp "$HOST_CONFIG_DIR/$CREDENTIAL" "$CRED_DIR/$CREDENTIAL" || stop
        record staged_names "$(ls -A "$CRED_DIR" | tr '\n' ',' | sed 's/,$//')"
        run_sbx cp "$CRED_DIR" "$SANDBOX":"$VM_CONFIG" >"$WORK/cp-cred-$backend.txt" 2>&1
        record "cp_cred_${backend}_exit" "$?"
        run_sbx exec "$SANDBOX" sh -c "
            sudo chown -R \"\$(id -un):\$(id -gn)\" $VM_CONFIG
            chmod 700 $VM_CONFIG
            chmod 600 $VM_CONFIG/$CREDENTIAL
            printf 'config_dir_names=%s\n' \"\$(ls -A $VM_CONFIG | tr '\n' ',' | sed 's/,\$//')\"
            printf 'full_cagent_copied=%s\n' \"\$(test -e $VM_CONFIG/user-uuid -o \
                -e $VM_CONFIG/.cagent_tour && echo yes || echo no)\"
        " >"$WORK/credstate-$backend.txt" 2>&1
        record "credstate_${backend}_exit" "$?"
        CONFIG_FLAG="--config-dir $VM_CONFIG"
    fi

    run_sbx exec "$SANDBOX" sh -c "
        printf 'stdin_is_tty=%s\n' \"\$(test -t 0 && echo yes || echo no)\"
        printf 'workload_user=%s\n' \"\$(id -un)\"
        printf 'sudo_resolves_to=%s\n' \"\$(sudo id -un 2>/dev/null)\"
        printf 'agent_binary=%s\n' \"\$($AGENT_BIN version 2>/dev/null | head -1 | awk '{print \$NF}')\"
    " >"$WORK/vmstate-$backend.txt" 2>"$WORK/vmstate-$backend.err"
    record "vmstate_${backend}_exit" "$?"

    # ==============================================================================================
    # POSITIVE CONTROL FIRST. The legitimate agent path must produce the mediation signal on the
    # same endpoint and request family, in this same sandbox, BEFORE the workload sends anything.
    # ==============================================================================================
    run_sbx exec "$SANDBOX" sh -c "
        cd /tmp && exec $AGENT_BIN run --exec --json $SAFETY $CONFIG_FLAG \
            /tmp/agent.yaml \"\$(cat /tmp/dca-g9-tasks/trivial.txt)\" </dev/null
    " >"$WORK/control-$backend.json" 2>"$WORK/control-$backend.err"
    record "control_${backend}_exit" "$?"

    # Captured BEFORE the workload runs, so the host entry is the agent's own traffic.
    run_sbx policy log "$SANDBOX" --json >"$WORK/policylog-control-$backend.json" 2>&1
    record "policylog_control_${backend}_exit" "$?"

    # ==============================================================================================
    # WORKLOAD: a repository-controlled process, once without sudo and once with it.
    # ==============================================================================================
    run_sbx exec "$SANDBOX" sh -c "
        cd /tmp && python3 /tmp/dca-g9-workload.py --probes /tmp/dca-g9-probes.json \
            --oracle /tmp/dca-g9-oracle.py --backend $backend --privilege user \
            --scanners /tmp/dca-g1b-scan.py,/tmp/dca-g2-scan.py
    " >"$WORK/workload-user-$backend.jsonl" 2>"$WORK/workload-user-$backend.err"
    record "workload_user_${backend}_exit" "$?"

    run_sbx exec "$SANDBOX" sh -c "
        cd /tmp && sudo python3 /tmp/dca-g9-workload.py --probes /tmp/dca-g9-probes.json \
            --oracle /tmp/dca-g9-oracle.py --backend $backend --privilege sudo \
            --scanners /tmp/dca-g1b-scan.py,/tmp/dca-g2-scan.py
    " >"$WORK/workload-sudo-$backend.jsonl" 2>"$WORK/workload-sudo-$backend.err"
    record "workload_sudo_${backend}_exit" "$?"

    run_sbx policy log "$SANDBOX" --json >"$WORK/policylog-after-$backend.json" 2>&1
    record "policylog_after_${backend}_exit" "$?"

    run_sbx rm --force "$SANDBOX" >"$WORK/rm-$SANDBOX.txt" 2>&1
    record "rm_${SANDBOX}_exit" "$?"
    rm -f "$WORK/created-$SANDBOX"
    [ -n "$CRED_DIR" ] && { rm -rf "$CRED_DIR"; CRED_DIR=""; }

    run_sbx ls --json >"$WORK/ls-between-$backend.json" 2>&1
    record "ls_between_${backend}_exit" "$?"
done

cleanup
trap - EXIT INT TERM

python3 gates/G9/record.py "$OBS" "$WORK"
