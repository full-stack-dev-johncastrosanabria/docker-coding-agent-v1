#!/bin/sh
# Gate G2: ChatGPT OAuth isolation and credential-mechanism selection (tasks.md T022).
#
# POSIX sh. Every sbx call goes through run_sbx, which removes SSH_AUTH_SOCK first (research R14).
#
# The question: can Codex run on Docker Sandboxes' HOST-SIDE, proxy-managed OpenAI OAuth with NO
# readable token material inside the VM? If yes, that mechanism is preferred for both profiles. If
# no, G3's already-proven trusted token-file fallback stands and untrusted Codex stays blocked.
#
# A FAIL here is a LEGITIMATE outcome and must never be avoided by weakening the test. The single
# thing that makes this gate meaningful is that NO chatgpt-auth.json is provisioned: with the
# token-file fallback present, a successful run would prove nothing about the proxy. So the gate
# does not merely omit the copy - it proves the file exists NOWHERE in the VM.
#
# The secret scan reuses gates/G1b/scan.py's traversal unchanged, because that code already fixes
# the three defects that made an earlier scan lie: `~` resolving to ROOT's home under sudo,
# overlapping roots double-counting matches, and a Bearer REFERENCE being read as inline material.
# Only the patterns differ, and they are aimed at what the ChatGPT store actually holds.
#
# A negative scan is only meaningful if the scanner can find a token when one is really there, so a
# clearly synthetic canary is planted and required to be detected FIRST, then removed before the
# real scan so it cannot inflate the result.
#
# Two consecutive fresh sandboxes, one live at a time, mountless, --skills off, from the exact
# pinned sandbox_bases.codex base, under the network policy accepted G4 proved for codex/trusted.

set -u

ROOT=$(CDPATH='' cd -- "$(dirname -- "$0")/../.." && pwd) || exit 1
cd "$ROOT" || exit 1

WORK=gates/G2/work
OBS=$WORK/observations.env
KIT=gates/G6/work/kit
AGENT=gates/G2/agent.yaml
GOVERNANCE_PROBE=example.invalid:443

SANDBOX_A=dca-g2-a
SANDBOX_B=dca-g2-b
AGENT_BIN=/opt/dca/bin/docker-agent
CREDENTIAL=chatgpt-auth.json

VM_CONFIG=/tmp/dca-g2-cagent
VM_CANARY=/tmp/dca-g2-canary.json
MARKER=DCA-G2-OK
CANARY_MARKER=DCA-G2-CANARY-NOT-A-REAL-TOKEN

record() {
    printf '%s=%s\n' "$1" "$2" >>"$OBS"
}

run_sbx() {
    env -u SSH_AUTH_SOCK sbx "$@"
}

cleanup() {
    for name in "$SANDBOX_A" "$SANDBOX_B"; do
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
}

stop() {
    record stopped_early true
    cleanup
    trap - EXIT INT TERM
    python3 gates/G2/record.py "$OBS" "$WORK"
    exit 1
}

case $WORK in
    gates/G2/work) rm -rf "$WORK" || exit 1 ;;
    *) echo "refusing to clear unexpected work dir: $WORK" >&2; exit 1 ;;
esac
mkdir -p "$WORK" || exit 1
: >"$OBS"

# --- T022 runs only on a G3 PASS -------------------------------------------------------------------
python3 gates/G2/record.py --requires-g3 || { record g3_not_pass true; stop; }

# --- read-only preflight, with zero sandboxes ------------------------------------------------------
mkdir -p "$WORK/envprobe" || exit 1
cat >"$WORK/envprobe/sbx" <<'STUB'
#!/bin/sh
if [ -n "${SSH_AUTH_SOCK:-}" ]; then echo present; else echo removed; fi
STUB
chmod +x "$WORK/envprobe/sbx" || exit 1
record sbx_env_ssh_auth_sock "$(
    PATH="$ROOT/$WORK/envprobe:$PATH"
    SSH_AUTH_SOCK=/nonexistent/g2-env-probe.sock
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

python3 gates/G2/record.py --preflight "$OBS" "$WORK" || stop

# The host-side, proxy-managed credential this gate depends on. NAMES only: the value is never
# printed, read or recorded, and `sbx secret ls` does not expose it.
run_sbx secret ls >"$WORK/secrets.txt" 2>&1
record secrets_exit "$?"

POLICY_ALLOW=$(python3 gates/G2/record.py --policy allow) || stop
POLICY_DENY=$(python3 gates/G2/record.py --policy deny) || stop
record policy_allow "$POLICY_ALLOW"
record policy_deny "$POLICY_DENY"
record safety_flag strict
record selected_model "$(python3 gates/G2/record.py --model)"

[ -d "$KIT" ] || { record kit_missing true; stop; }

mkdir -p "$WORK/tasks" || exit 1
printf 'Reply with exactly the token %s and nothing else. Do not use any tool.\n' "$MARKER" \
    >"$WORK/tasks/trivial.txt"
# NOTHING token-shaped is copied into the sandbox. The canary is written IN THE VM at scan time
# (see below) and removed again before the real scan, so no fixture of the gate's own making is
# ever present while the decision scan runs.

trap 'cleanup' EXIT INT TERM

# ==================================================================================================
# Two consecutive COMPLETELY FRESH sandboxes, with NO credential file anywhere.
# ==================================================================================================
for slot in a b; do
    case $slot in
        a) SANDBOX=$SANDBOX_A ;;
        b) SANDBOX=$SANDBOX_B ;;
    esac

    run_sbx ls --json >"$WORK/ls-before-$slot.json" 2>&1
    record "ls_before_${slot}_exit" "$?"

    run_sbx create docker-agent --name "$SANDBOX" --skills off --kit "./$KIT" \
        >"$WORK/create-$SANDBOX.txt" 2>&1
    create_status=$?
    record "create_${SANDBOX}_exit" "$create_status"
    [ "$create_status" -eq 0 ] && : >"$WORK/created-$SANDBOX"
    [ "$create_status" -eq 0 ] || stop

    if [ "$slot" = a ]; then
        run_sbx template ls --json >"$WORK/templates.json" 2>"$WORK/templates.err"
        record templates_exit "$?"
        record sbx_resolved_base "$(sed -n 's/.*\(docker\/sandbox-templates:[a-z0-9.-]*\).*/\1/p' \
            "$WORK/create-$SANDBOX.txt" | head -1)"
    fi

    rules_status=0
    run_sbx policy allow network --sandbox "$SANDBOX" "$POLICY_ALLOW" \
        >"$WORK/allow-$SANDBOX.txt" 2>&1 || rules_status=1
    if [ -n "$POLICY_DENY" ]; then
        run_sbx policy deny network --sandbox "$SANDBOX" "$POLICY_DENY" \
            >"$WORK/deny-$SANDBOX.txt" 2>&1 || rules_status=1
    fi
    record "policy_rules_${slot}_exit" "$rules_status"
    [ "$slot" = a ] && record policy_rules_exit "$rules_status"

    # NO credential is copied. The config directory is created EMPTY, and the gate proves the
    # credential file exists nowhere in the VM rather than merely trusting that it skipped a copy.
    run_sbx cp "$AGENT" "$SANDBOX":/tmp/agent.yaml >"$WORK/cp-agent-$slot.txt" 2>&1
    record "cp_agent_${slot}_exit" "$?"
    run_sbx cp "$WORK/tasks" "$SANDBOX":/tmp/dca-g2-tasks >"$WORK/cp-tasks-$slot.txt" 2>&1
    record "cp_tasks_${slot}_exit" "$?"
    run_sbx cp gates/G2/scan.py "$SANDBOX":/tmp/dca-g2-scan.py >"$WORK/cp-scan-$slot.txt" 2>&1
    record "cp_scan_${slot}_exit" "$?"
    run_sbx cp gates/G1b/scan.py "$SANDBOX":/tmp/dca-g1b-scan.py >"$WORK/cp-shared-$slot.txt" 2>&1
    record "cp_shared_${slot}_exit" "$?"

    run_sbx exec "$SANDBOX" sh -c "
        mkdir -p $VM_CONFIG && chmod 700 $VM_CONFIG
        printf 'config_dir_entries=%s\n' \"\$(ls -A $VM_CONFIG | wc -l | tr -d ' ')\"
        printf 'credential_files_found=%s\n' \"\$(sudo find / -xdev -name $CREDENTIAL 2>/dev/null | wc -l | tr -d ' ')\"
        printf 'credential_paths=%s\n' \"\$(sudo find / -xdev -name $CREDENTIAL 2>/dev/null | head -5 | tr '\n' ',' | sed 's/,\$//')\"
        printf 'stdin_is_tty=%s\n' \"\$(test -t 0 && echo yes || echo no)\"
        for name in OPENAI_API_KEY OPENAI_BASE_URL OPENAI_ORG_ID ANTHROPIC_API_KEY ANTHROPIC_AUTH_TOKEN CLAUDE_API_KEY; do
            if env | grep -q \"^\$name=\"; then
                if [ \"\$(printenv \"\$name\" | wc -c | tr -d ' ')\" -le 1 ]; then cls=empty; else cls=present; fi
            else
                cls=absent
            fi
            printf 'apikey_%s=%s\n' \"\$name\" \"\$cls\"
        done
        printf 'agent_binary=%s\n' \"\$($AGENT_BIN version 2>/dev/null | head -1 | awk '{print \$NF}')\"
    " >"$WORK/vmstate-$slot.txt" 2>"$WORK/vmstate-$slot.err"
    record "vmstate_${slot}_exit" "$?"

    # --- does the native provider work on the PROXY-MANAGED credential alone? -----------------------
    run_sbx exec "$SANDBOX" sh -c "
        cd /tmp && exec $AGENT_BIN run --exec --json --safety strict --config-dir $VM_CONFIG \
            /tmp/agent.yaml \"\$(cat /tmp/dca-g2-tasks/trivial.txt)\" </dev/null
    " >"$WORK/task-$slot.json" 2>"$WORK/task-$slot.err"
    record "task_${slot}_exit" "$?"

    # --- the canary control FIRST: a scanner that matches nothing must not look like isolation -------
    # The canary is WRITTEN HERE, inside the VM, rather than copied in as a file. A canary shipped
    # into the sandbox survives into the real scan below and fires the very patterns the gate
    # decides on, which manufactures a FAIL that says nothing about the proxy.
    run_sbx exec "$SANDBOX" sh -c "
        printf '%s' '{\"access_token\":\"eyJDCAG2CANARYHEADER.eyJDCAG2CANARYPAYLOAD.DCAG2CANARYSIG\",\"refresh_token\":\"eyJDCAG2CANARYREFRESH.eyJDCAG2CANARYPAYLOAD.DCAG2CANARYSIG\",\"last_refresh\":\"$CANARY_MARKER\"}' >$VM_CANARY
        sudo python3 /tmp/dca-g2-scan.py --shared /tmp/dca-g1b-scan.py tmp
    " >"$WORK/scan-canary-$slot.txt" 2>"$WORK/scan-canary-$slot.err"
    record "scan_canary_${slot}_exit" "$?"

    # --- then the REAL scan, with the canary gone so it cannot inflate the result --------------------
    # If the canary somehow survives, the scan is NOT run at all: a leftover would fire the decision
    # patterns, and a FAIL produced by the gate's own fixture is worse than no result.
    run_sbx exec "$SANDBOX" sh -c "
        rm -f $VM_CANARY
        if [ -e $VM_CANARY ]; then echo 'canary_removed=no' >&2; exit 3; fi
        echo 'canary_removed=yes' >&2
        sudo python3 /tmp/dca-g2-scan.py --shared /tmp/dca-g1b-scan.py
    " >"$WORK/scan-$slot.txt" 2>"$WORK/scan-$slot.err"
    record "scan_${slot}_exit" "$?"

    run_sbx rm --force "$SANDBOX" >"$WORK/rm-$SANDBOX.txt" 2>&1
    record "rm_${SANDBOX}_exit" "$?"
    rm -f "$WORK/created-$SANDBOX"

    run_sbx ls --json >"$WORK/ls-between-$slot.json" 2>&1
    record "ls_between_${slot}_exit" "$?"
done

cleanup
trap - EXIT INT TERM

python3 gates/G2/record.py "$OBS" "$WORK"
