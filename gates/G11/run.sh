#!/bin/sh
# Gate G11 part A: event-stream integrity for both available backends (tasks.md T020).
#
# POSIX sh. Every sbx call goes through run_sbx, which removes SSH_AUTH_SOCK first (research R14).
#
# The question: can host-side processing rely on Docker Agent's typed OUTER event stream before any
# launcher enforcement exists? Four things must hold on EVERY available backend:
#
#   1. a known number N of real tool calls produces exactly N countable outer events;
#   2. JSON printed BY a tool stays tool output and never becomes an outer event;
#   3. malformed and truncated streams are classified fail-closed (host-simulated, no extra runs);
#   4. killing Docker Agent abruptly is `abnormal` and can never read as success.
#
# Criterion 1's ground truth is NOT taken from the stream. Each tool call leaves its own marker in
# the VM, so the number of calls that really happened is established from the filesystem before the
# parser is allowed an opinion. A gate that counted the stream and then checked the stream would
# prove nothing.
#
# Criterion 3 is derived on the host from the sanitized criterion-1 capture, because mutating stream
# bytes needs no model. Criterion 4 is live, once per backend.
#
# One sandbox at a time, mountless, --skills off, from each backend's exact pinned base, under the
# accepted G4 policy for that backend. Codex additionally runs --safety strict on every invocation
# and carries only the minimal chatgpt-auth.json the accepted G2 decision selected.

set -u

ROOT=$(CDPATH='' cd -- "$(dirname -- "$0")/../.." && pwd) || exit 1
cd "$ROOT" || exit 1

WORK=gates/G11/work
OBS=$WORK/observations.env
KIT=gates/G6/work/kit
CAPTURES=gates/G11/captures
GOVERNANCE_PROBE=example.invalid:443

AGENT_BIN=/opt/dca/bin/docker-agent
CREDENTIAL=chatgpt-auth.json
HOST_CONFIG_DIR=$HOME/.config/cagent
VM_CONFIG=/tmp/dca-g11-cagent
SPOOF_IN_VM=/tmp/dca-g11-spoof.txt
MARKER=DCA-G11-OK
EXPECTED_N=3

record() {
    printf '%s=%s\n' "$1" "$2" >>"$OBS"
}

run_sbx() {
    env -u SSH_AUTH_SOCK sbx "$@"
}

cleanup() {
    for name in dca-g11-claude dca-g11-codex; do
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
    python3 gates/G11/record.py "$OBS" "$WORK"
    exit 1
}

case $WORK in
    gates/G11/work) rm -rf "$WORK" || exit 1 ;;
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
    SSH_AUTH_SOCK=/nonexistent/g11-env-probe.sock
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

python3 gates/G11/record.py --preflight "$OBS" "$WORK" || stop

# --- which backends are available, from the accepted availability evidence --------------------------
AVAILABLE=$(python3 gates/G11/record.py --available) || stop
record available_backends "$AVAILABLE"
record expected_tool_calls "$EXPECTED_N"
[ -n "$AVAILABLE" ] || { record no_backend_available true; stop; }
[ -d "$KIT" ] || { record kit_missing true; stop; }

# --- the task texts, written to files so no quoting can split them into several messages ------------
mkdir -p "$WORK/tasks" || exit 1
cat >"$WORK/tasks/claude-count.txt" <<TASK
Use the Bash tool exactly three times, one Bash call per command, in this order:
touch /tmp/dca-g11-a
touch /tmp/dca-g11-b
touch /tmp/dca-g11-c
Never combine them into one command with && or ; or newlines. Use one separate Bash tool call for
each of the three commands. Then reply with exactly $MARKER and nothing else.
TASK
cat >"$WORK/tasks/claude-spoof.txt" <<TASK
Use the Bash tool exactly once to run this command: cat $SPOOF_IN_VM
Then reply with exactly $MARKER and nothing else. Do not use any other tool.
TASK
cat >"$WORK/tasks/codex-count.txt" <<TASK
Use your file-writing tool exactly three times, one call per file, to create these three files in
the current directory, each containing the single line OK:
dca-g11-a.txt
dca-g11-b.txt
dca-g11-c.txt
Use one separate file-writing tool call for each file. Then reply with exactly $MARKER and nothing
else. Do not read, list or write anything else.
TASK
cat >"$WORK/tasks/codex-spoof.txt" <<TASK
Use your file-reading tool exactly once to read the file $SPOOF_IN_VM.
Then reply with exactly $MARKER and nothing else. Do not use any other tool.
TASK

CRED_DIR=""
trap 'cleanup' EXIT INT TERM

# ==================================================================================================
# One backend at a time, one sandbox at a time.
# ==================================================================================================
for backend in $AVAILABLE; do
    case $backend in
        claude)
            SANDBOX=dca-g11-claude
            TEMPLATE=claude
            AGENT=gates/G11/agent-claude.yaml
            SAFETY=""
            ;;
        codex)
            SANDBOX=dca-g11-codex
            TEMPLATE=docker-agent
            AGENT=gates/G11/agent-codex.yaml
            SAFETY="--safety strict"
            ;;
        *) record "unknown_backend_$backend" true; stop ;;
    esac

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

    POLICY_ALLOW=$(python3 gates/G11/record.py --policy "$backend" allow) || stop
    POLICY_DENY=$(python3 gates/G11/record.py --policy "$backend" deny) || stop
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
    run_sbx cp gates/G11/spoof-lines.txt "$SANDBOX":"$SPOOF_IN_VM" \
        >"$WORK/cp-spoof-$backend.txt" 2>&1
    record "cp_spoof_${backend}_exit" "$?"
    run_sbx cp "$WORK/tasks" "$SANDBOX":/tmp/dca-g11-tasks >"$WORK/cp-tasks-$backend.txt" 2>&1
    record "cp_tasks_${backend}_exit" "$?"
    # The exact safety flag this backend's invocations carry, recorded so the evidence states it
    # rather than implying it. Empty for Claude, whose harness has no --safety flag.
    record "safety_flag_$backend" "$SAFETY"

    CONFIG_FLAG=""
    if [ "$backend" = claude ]; then
        # The accepted Claude path: the allow rules live in the ROOT-OWNED managed layer, which is
        # how G1c made non-interactive tool use work at all. Only the two G11 commands are allowed.
        run_sbx cp gates/G11/managed-settings.json "$SANDBOX":/tmp/g11-managed-settings.json \
            >"$WORK/cp-managed-$backend.txt" 2>&1
        record "cp_managed_${backend}_exit" "$?"
        run_sbx exec "$SANDBOX" sh -c "
            set -e
            sudo install -d -m 0755 -o root -g root /etc/claude-code
            sudo install -m 0644 -o root -g root /tmp/g11-managed-settings.json \
                /etc/claude-code/managed-settings.json
            sudo rm -f /tmp/g11-managed-settings.json
            printf 'managed_settings_owner=%s\n' \"\$(stat -c '%U:%G:%a' \
                /etc/claude-code/managed-settings.json)\"
        " >"$WORK/install-$backend.txt" 2>&1
        record "install_${backend}_exit" "$?"
    else
        # The accepted Codex path (G2 decision: token-file-trusted-only). ONLY chatgpt-auth.json is
        # staged; the full ~/.config/cagent is never copied.
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

        # The approving pre_tool_use hook at the FIXED path the agent config names. Under
        # --safety strict the native pipeline rejects a call with no decision (G3 case C), so
        # without this the probe dispatches zero tool calls and criterion 1 is unevaluable.
        # --safety strict still goes on every command line and is recorded.
        run_sbx cp gates/G11/hooks/allow.sh "$SANDBOX":/tmp/dca-g11-hook \
            >"$WORK/cp-hook-$backend.txt" 2>&1
        record "cp_hook_${backend}_exit" "$?"
        run_sbx exec "$SANDBOX" sh -c "chmod 0755 /tmp/dca-g11-hook && ls -l /tmp/dca-g11-hook" \
            >"$WORK/hook-$backend.txt" 2>&1
        record "hook_${backend}_exit" "$?"
    fi

    run_sbx exec "$SANDBOX" sh -c "
        printf 'stdin_is_tty=%s\n' \"\$(test -t 0 && echo yes || echo no)\"
        printf 'agent_binary=%s\n' \"\$($AGENT_BIN version 2>/dev/null | head -1 | awk '{print \$NF}')\"
    " >"$WORK/vmstate-$backend.txt" 2>"$WORK/vmstate-$backend.err"
    record "vmstate_${backend}_exit" "$?"

    # --- CRITERION 1: a known N real tool calls ------------------------------------------------------
    run_sbx exec "$SANDBOX" sh -c "
        cd /tmp && exec $AGENT_BIN run --exec --json $SAFETY $CONFIG_FLAG \
            /tmp/agent.yaml \"\$(cat /tmp/dca-g11-tasks/$backend-count.txt)\" </dev/null
    " >"$WORK/count-$backend.json" 2>"$WORK/count-$backend.err"
    record "count_${backend}_exit" "$?"

    # GROUND TRUTH, taken from the filesystem rather than from the stream the parser will read.
    run_sbx exec "$SANDBOX" sh -c "
        printf 'markers=%s\n' \"\$(ls -1 /tmp/dca-g11-a /tmp/dca-g11-b /tmp/dca-g11-c \
            /tmp/dca-g11-a.txt /tmp/dca-g11-b.txt /tmp/dca-g11-c.txt 2>/dev/null | wc -l | tr -d ' ')\"
    " >"$WORK/markers-$backend.txt" 2>"$WORK/markers-$backend.err"
    record "markers_${backend}_exit" "$?"

    # --- CRITERION 2: a tool PRINTS lines that imitate real events -----------------------------------
    run_sbx exec "$SANDBOX" sh -c "
        cd /tmp && exec $AGENT_BIN run --exec --json $SAFETY $CONFIG_FLAG \
            /tmp/agent.yaml \"\$(cat /tmp/dca-g11-tasks/$backend-spoof.txt)\" </dev/null
    " >"$WORK/spoof-$backend.json" 2>"$WORK/spoof-$backend.err"
    record "spoof_${backend}_exit" "$?"

    # --- CRITERION 4: abrupt Docker Agent termination ------------------------------------------------
    # Started from the host, then the Docker Agent process is killed inside the VM before it can
    # finish. `pkill -x` matches the process NAME exactly, so the sh running it cannot kill itself.
    run_sbx exec "$SANDBOX" sh -c "
        cd /tmp && exec $AGENT_BIN run --exec --json $SAFETY $CONFIG_FLAG \
            /tmp/agent.yaml \"\$(cat /tmp/dca-g11-tasks/$backend-count.txt)\" </dev/null
    " >"$WORK/kill-$backend.json" 2>"$WORK/kill-$backend.err" &
    kill_pid=$!
    waited=0
    while [ "$waited" -lt 120 ]; do
        if grep -q '"type":"stream_started"' "$WORK/kill-$backend.json" 2>/dev/null; then break; fi
        sleep 2
        waited=$((waited + 2))
    done
    record "kill_${backend}_waited" "$waited"
    record "kill_${backend}_stream_started" \
        "$(grep -c '"type":"stream_started"' "$WORK/kill-$backend.json" 2>/dev/null || echo 0)"
    run_sbx exec "$SANDBOX" sh -c 'sudo pkill -9 -x docker-agent' \
        >"$WORK/killsignal-$backend.txt" 2>&1
    record "kill_${backend}_signal_exit" "$?"
    wait "$kill_pid"
    record "kill_${backend}_agent_exit" "$?"

    run_sbx rm --force "$SANDBOX" >"$WORK/rm-$SANDBOX.txt" 2>&1
    record "rm_${SANDBOX}_exit" "$?"
    rm -f "$WORK/created-$SANDBOX"
    [ -n "$CRED_DIR" ] && { rm -rf "$CRED_DIR"; CRED_DIR=""; }

    run_sbx ls --json >"$WORK/ls-between-$backend.json" 2>&1
    record "ls_between_${backend}_exit" "$?"

    # --- sanitize, then derive CRITERION 3 on the host -----------------------------------------------
    mkdir -p "$CAPTURES/$backend" || stop
    for capture in count spoof kill; do
        python3 gates/G11/sanitize.py "$WORK/$capture-$backend.json" \
            "$CAPTURES/$backend/$capture.jsonl" >"$WORK/sanitize-$capture-$backend.txt" 2>&1
        record "sanitize_${capture}_${backend}_exit" "$?"
    done
    python3 gates/G11/record.py --derive "$CAPTURES/$backend" \
        >"$WORK/derive-$backend.txt" 2>&1
    record "derive_${backend}_exit" "$?"
done

cleanup
trap - EXIT INT TERM

python3 gates/G11/record.py "$OBS" "$WORK"
