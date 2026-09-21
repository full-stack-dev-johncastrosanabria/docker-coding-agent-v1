#!/bin/sh
# Gate G3: native ChatGPT availability and the trusted token-file fallback (tasks.md T019).
#
# POSIX sh. Every sbx call goes through run_sbx, which removes SSH_AUTH_SOCK first (research R14).
#
# Three properties, in one pass:
#
#   1. AVAILABILITY - the developer's ChatGPT sign-in drives the NATIVE Docker Agent chatgpt
#      provider (no harness, no API key) in a completely fresh pinned sandbox.
#   2. TRUSTED FALLBACK PROVISIONING - it does so from a config directory holding ONLY
#      chatgpt-auth.json, non-interactively (stdin /dev/null, no TTY), in TWO consecutive
#      completely fresh sandboxes, with the second created only after the first is removed by name.
#   3. APPROVAL PIPELINE - under `--safety strict` the native pre_tool_use pipeline is fail-closed:
#      an explicit allow executes, exit 2 blocks, NO decision blocks, and a user config asking for
#      `safety: autonomous` + `yolo: true` cannot defeat the command line.
#
# The credential never lands in the repository. It is staged in a mode-0700 temporary directory
# outside the tree, copied in with `sbx cp`, and the staging directory is removed on every exit
# path. Its CONTENTS are never read, printed, hashed or recorded - only presence, mode and the fact
# that the staged directory holds exactly one entry, which is what proves the full
# ~/.config/cagent was not copied.
#
# CASE D is behavioral, not a reported label: under `autonomous` every tool call is auto-approved
# and the hook is never consulted, so the exit-2 hook still blocking the write is what proves the
# session really ran strict.
#
# One live sandbox at a time, mountless, --skills off, from the exact pinned sandbox_bases.codex
# base, under the network policy accepted G4 proved for codex/trusted. Created and removed by name.

set -u

ROOT=$(CDPATH='' cd -- "$(dirname -- "$0")/../.." && pwd) || exit 1
cd "$ROOT" || exit 1

WORK=gates/G3/work
OBS=$WORK/observations.env
KIT=gates/G6/work/kit
AGENT=gates/G3/agent.yaml
PROBE=gates/G3/probe.yaml
GOVERNANCE_PROBE=example.invalid:443
REFRESH_CANDIDATE=auth.openai.com:443

SANDBOX_A=dca-g3-a
SANDBOX_B=dca-g3-b
AGENT_BIN=/opt/dca/bin/docker-agent
HOST_CONFIG_DIR=$HOME/.config/cagent
CREDENTIAL=chatgpt-auth.json

VM_CONFIG=/tmp/dca-g3-cagent
VM_CONFIG_HOSTILE=/tmp/dca-g3-cagent-hostile
VM_WS=/tmp/dca-g3-ws
VM_HOOK=/tmp/dca-g3-hook
MARKER=DCA-G3-OK
WRITE_MARKER=DCA-G3-WRITE

record() {
    printf '%s=%s\n' "$1" "$2" >>"$OBS"
}

run_sbx() {
    env -u SSH_AUTH_SOCK sbx "$@"
}

# The staged credential directory lives outside the repository and is removed on every exit path,
# including a failure or an interrupt.
CRED_DIR=""
CRED_DIR_HOSTILE=""
scrub_credentials() {
    [ -n "$CRED_DIR" ] && [ -d "$CRED_DIR" ] && rm -rf "$CRED_DIR"
    [ -n "$CRED_DIR_HOSTILE" ] && [ -d "$CRED_DIR_HOSTILE" ] && rm -rf "$CRED_DIR_HOSTILE"
    CRED_DIR=""
    CRED_DIR_HOSTILE=""
}

cleanup() {
    for name in "$SANDBOX_A" "$SANDBOX_B"; do
        if [ -f "$WORK/created-$name" ]; then
            run_sbx rm --force "$name" >"$WORK/sweep-$name.txt" 2>&1
        fi
    done
    scrub_credentials
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
    python3 gates/G3/record.py "$OBS" "$WORK"
    exit 1
}

case $WORK in
    gates/G3/work) rm -rf "$WORK" || exit 1 ;;
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
    SSH_AUTH_SOCK=/nonexistent/g3-env-probe.sock
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

python3 gates/G3/record.py --preflight "$OBS" "$WORK" || stop

# --- the host side: which models the signed-in plan actually offers ---------------------------------
# Read first, select second. The evidence records the list the provider reported, so a selection
# can be checked against it rather than taken on trust.
docker agent models --provider chatgpt >"$WORK/models.txt" 2>"$WORK/models.err"
record models_exit "$?"
# The listing is captured for the record. It is NOT the authority: it advertises a model the Codex
# backend rejects for a ChatGPT-account sign-in, so the selection is made against the RUNTIME below.
record listed_preferred "$(python3 gates/G3/record.py --select-model "$WORK/models.txt" 2>/dev/null || echo none)"
docker agent version >"$WORK/host-agent-version.txt" 2>&1
record host_agent_version_exit "$?"

# Provider API-key variables are checked for ABSENCE by name. A value is never read.
python3 gates/G3/record.py --apikeys >"$WORK/apikeys-host.txt" 2>&1
record apikeys_host_exit "$?"

POLICY_ALLOW=$(python3 gates/G3/record.py --policy allow) || stop
POLICY_DENY=$(python3 gates/G3/record.py --policy deny) || stop
record policy_allow "$POLICY_ALLOW"
record policy_deny "$POLICY_DENY"
# Every native invocation below passes this on the command line, never relying on a config
# default. The evidence records the literal flag so the claim can be checked, and CASE D
# proves behaviorally that it actually held.
record safety_flag strict

[ -d "$KIT" ] || { record kit_missing true; stop; }

trap 'cleanup' EXIT INT TERM

# --- stage the MINIMAL credential directory, outside the repository --------------------------------
[ -f "$HOST_CONFIG_DIR/$CREDENTIAL" ] || { record host_credential_missing true; stop; }
record host_config_dir_entries "$(ls -A "$HOST_CONFIG_DIR" | wc -l | tr -d ' ')"
record host_credential_mode "$(stat -f '%Lp' "$HOST_CONFIG_DIR/$CREDENTIAL" 2>/dev/null \
    || stat -c '%a' "$HOST_CONFIG_DIR/$CREDENTIAL" 2>/dev/null)"

CRED_DIR=$(mktemp -d) || stop
chmod 700 "$CRED_DIR" || stop
cp "$HOST_CONFIG_DIR/$CREDENTIAL" "$CRED_DIR/$CREDENTIAL" || { record credential_stage_failed true; stop; }
record staged_entries "$(ls -A "$CRED_DIR" | wc -l | tr -d ' ')"
record staged_names "$(ls -A "$CRED_DIR" | tr '\n' ',' | sed 's/,$//')"

# CASE D's config directory: the same credential PLUS a user config asking for the weakest settings.
CRED_DIR_HOSTILE=$(mktemp -d) || stop
chmod 700 "$CRED_DIR_HOSTILE" || stop
cp "$HOST_CONFIG_DIR/$CREDENTIAL" "$CRED_DIR_HOSTILE/$CREDENTIAL" || stop
cp gates/G3/hostile-user-config.yaml "$CRED_DIR_HOSTILE/config.yaml" || stop
record staged_hostile_names "$(ls -A "$CRED_DIR_HOSTILE" | tr '\n' ',' | sed 's/,$//')"

# --- the task texts, written to files so no quoting can split them into several messages ----------
mkdir -p "$WORK/tasks" || stop
printf 'Reply with exactly the token %s and nothing else. Do not use any tool.\n' "$MARKER" \
    >"$WORK/tasks/trivial.txt"
for case_name in allow exit2 no-decision hostile; do
    printf 'Use your file-writing tool exactly once to create the file %s.txt in the current directory, containing the single line %s. Then stop. Do not read, list or write anything else.\n' \
        "$case_name" "$WRITE_MARKER" >"$WORK/tasks/$case_name.txt"
done

# ==================================================================================================
# Two consecutive COMPLETELY FRESH sandboxes. The second is created only after the first is gone.
# ==================================================================================================
for slot in a b; do
    case $slot in
        a) SANDBOX=$SANDBOX_A ;;
        b) SANDBOX=$SANDBOX_B ;;
    esac

    # One live sandbox at a time: nothing may exist as this one is created.
    run_sbx ls --json >"$WORK/ls-before-$slot.json" 2>&1
    record "ls_before_${slot}_exit" "$?"

    run_sbx create docker-agent --name "$SANDBOX" --skills off --kit "./$KIT" \
        >"$WORK/create-$SANDBOX.txt" 2>&1
    create_status=$?
    # Keyed by NAME: the shared cleanup rule pairs create_<name>_exit with rm_<name>_exit to decide
    # which sandboxes this gate is answerable for.
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

    # The refresh-promotion candidate must be DENIED for this profile. If the provider turns out to
    # need it in-VM, the runs below fail and that is the evidence a promotion would rest on.
    run_sbx policy check network "$REFRESH_CANDIDATE" --sandbox "$SANDBOX" --json \
        >"$WORK/refresh-check-$slot.json" 2>"$WORK/refresh-check-$slot.err"
    record "refresh_check_${slot}_exit" "$?"

    # --- the minimal config directory, and nothing else -------------------------------------------
    run_sbx cp "$CRED_DIR" "$SANDBOX":"$VM_CONFIG" >"$WORK/cp-cred-$slot.txt" 2>&1
    record "cp_cred_${slot}_exit" "$?"
    # `sbx cp` carries the host mode across but not the owner, so a mode-0700 directory lands
    # unreadable by the workload user and the provider cannot even traverse it. Owner-only FOR THE
    # WORKLOAD USER is the state the launcher contract asks for, so set that explicitly.
    run_sbx exec "$SANDBOX" sh -c "
        sudo chown -R \"\$(id -un):\$(id -gn)\" $VM_CONFIG
        chmod 700 $VM_CONFIG
        chmod 600 $VM_CONFIG/$CREDENTIAL
    " >"$WORK/own-cred-$slot.txt" 2>&1
    record "own_cred_${slot}_exit" "$?"
    run_sbx cp "$AGENT" "$SANDBOX":/tmp/agent.yaml >"$WORK/cp-agent-$slot.txt" 2>&1
    record "cp_agent_${slot}_exit" "$?"
    run_sbx cp "$WORK/tasks" "$SANDBOX":/tmp/dca-g3-tasks >"$WORK/cp-tasks-$slot.txt" 2>&1
    record "cp_tasks_${slot}_exit" "$?"

    # What the VM actually holds: the config directory's exact contents, the absence of every
    # provider API-key variable by name, and that stdin is not a terminal.
    run_sbx exec "$SANDBOX" sh -c "
        printf 'config_dir_entries=%s\n' \"\$(ls -A $VM_CONFIG | wc -l | tr -d ' ')\"
        printf 'config_dir_names=%s\n' \"\$(ls -A $VM_CONFIG | tr '\n' ',' | sed 's/,\$//')\"
        printf 'credential_present=%s\n' \"\$(test -f $VM_CONFIG/$CREDENTIAL && echo yes || echo no)\"
        printf 'credential_mode=%s\n' \"\$(stat -c '%a' $VM_CONFIG/$CREDENTIAL 2>/dev/null)\"
        printf 'credential_owner=%s\n' \"\$(stat -c '%U:%G' $VM_CONFIG/$CREDENTIAL 2>/dev/null)\"
        printf 'full_cagent_copied=%s\n' \"\$(test -e $VM_CONFIG/user-uuid -o -e $VM_CONFIG/.cagent_tour && echo yes || echo no)\"
        printf 'stdin_is_tty=%s\n' \"\$(test -t 0 && echo yes || echo no)\"
        for name in OPENAI_API_KEY OPENAI_BASE_URL OPENAI_ORG_ID ANTHROPIC_API_KEY ANTHROPIC_AUTH_TOKEN CLAUDE_API_KEY; do
            if env | grep -q \"^\$name=\"; then
                if [ \"\$(printenv \"\$name\" | wc -c | tr -d ' ')\" -le 1 ]; then
                    cls=empty
                else
                    cls=present
                fi
            else
                cls=absent
            fi
            printf 'apikey_%s=%s\n' \"\$name\" \"\$cls\"
        done
        printf 'agent_binary=%s\n' \"\$($AGENT_BIN version 2>/dev/null | head -1 | awk '{print \$NF}')\"
    " >"$WORK/vmstate-$slot.txt" 2>"$WORK/vmstate-$slot.err"
    record "vmstate_${slot}_exit" "$?"

    # --- which model the BACKEND actually accepts, enumerated in the first sandbox -------------------
    # `docker agent models` lists gpt-5.6 and marks it default, but the Codex backend rejects it for
    # a ChatGPT account. So the candidates - the GPT-5.x identifiers present in the pinned artifact,
    # in T019's preference order - are tried against the real endpoint and the FIRST acceptance wins.
    # Enumeration stops there, so this costs a couple of trivial calls rather than one per candidate.
    if [ "$slot" = a ]; then
        : >"$WORK/model-probe.txt"
        RUNTIME_MODEL=""
        for cand in $(python3 gates/G3/record.py --candidates); do
            run_sbx exec "$SANDBOX" sh -c "
                cd /tmp && $AGENT_BIN run --exec --json --safety strict --config-dir $VM_CONFIG \
                    --model root=chatgpt/$cand /tmp/agent.yaml \
                    \"\$(cat /tmp/dca-g3-tasks/trivial.txt)\" </dev/null
            " >"$WORK/probe-$cand.json" 2>"$WORK/probe-$cand.err"
            probe_status=$?
            printf '%s=%s\n' "$cand" "$probe_status" >>"$WORK/model-probe.txt"
            if [ "$probe_status" -eq 0 ]; then
                RUNTIME_MODEL=$cand
                break
            fi
        done
        record runtime_selected_model "$RUNTIME_MODEL"
        [ -n "$RUNTIME_MODEL" ] || { record no_acceptable_model true; stop; }
    fi

    # --- the trivial native task, non-interactively -------------------------------------------------
    run_sbx exec "$SANDBOX" sh -c "
        cd /tmp && exec $AGENT_BIN run --exec --json --safety strict --config-dir $VM_CONFIG \
            /tmp/agent.yaml \"\$(cat /tmp/dca-g3-tasks/trivial.txt)\" </dev/null
    " >"$WORK/task-$slot.json" 2>"$WORK/task-$slot.err"
    record "task_${slot}_exit" "$?"

    # --- negative control: without the credential the identical task must NOT succeed ---------------
    if [ "$slot" = a ]; then
        run_sbx exec "$SANDBOX" sh -c "
            mkdir -p /tmp/dca-g3-nocred && chmod 700 /tmp/dca-g3-nocred
            cd /tmp && $AGENT_BIN run --exec --json --safety strict \
                --config-dir /tmp/dca-g3-nocred /tmp/agent.yaml \
                \"\$(cat /tmp/dca-g3-tasks/trivial.txt)\" </dev/null
        " >"$WORK/control-nocred.json" 2>"$WORK/control-nocred.err"
        record control_nocred_exit "$?"
    fi

    # --- the approval matrix, in the FIRST sandbox only --------------------------------------------
    if [ "$slot" = a ]; then
        run_sbx cp "$PROBE" "$SANDBOX":/tmp/probe.yaml >"$WORK/cp-probe.txt" 2>&1
        record cp_probe_exit "$?"
        run_sbx cp gates/G3/hooks "$SANDBOX":/tmp/dca-g3-hooks >"$WORK/cp-hooks.txt" 2>&1
        record cp_hooks_exit "$?"
        run_sbx cp "$CRED_DIR_HOSTILE" "$SANDBOX":"$VM_CONFIG_HOSTILE" \
            >"$WORK/cp-cred-hostile.txt" 2>&1
        record cp_cred_hostile_exit "$?"

        run_sbx exec "$SANDBOX" sh -c "
            sudo chown -R \"\$(id -un):\$(id -gn)\" $VM_CONFIG_HOSTILE
            chmod 700 $VM_CONFIG_HOSTILE
            chmod +x /tmp/dca-g3-hooks/*.sh
            mkdir -p $VM_WS
            printf 'hostile_config_entries=%s\n' \"\$(ls -A $VM_CONFIG_HOSTILE | tr '\n' ',' | sed 's/,\$//')\"
            printf 'hostile_config_safety=%s\n' \"\$(sed -n 's/^safety: *//p' $VM_CONFIG_HOSTILE/config.yaml)\"
            printf 'hostile_config_yolo=%s\n' \"\$(sed -n 's/^yolo: *//p' $VM_CONFIG_HOSTILE/config.yaml)\"
        " >"$WORK/approval-setup.txt" 2>"$WORK/approval-setup.err"
        record approval_setup_exit "$?"

        # allow -> must write; exit2 -> must not; no-decision -> must not; hostile -> must not,
        # because strict keeps the hook in the loop and the hook exits 2.
        for case_name in allow exit2 no-decision hostile; do
            case $case_name in
                allow)       hook=allow.sh;       config=$VM_CONFIG ;;
                exit2)       hook=exit2.sh;       config=$VM_CONFIG ;;
                no-decision) hook=no-decision.sh; config=$VM_CONFIG ;;
                hostile)     hook=exit2.sh;       config=$VM_CONFIG_HOSTILE ;;
            esac
            run_sbx exec "$SANDBOX" sh -c "
                cp /tmp/dca-g3-hooks/$hook $VM_HOOK && chmod +x $VM_HOOK
                rm -f $VM_WS/$case_name.txt
                cd $VM_WS && $AGENT_BIN run --exec --json --safety strict --config-dir $config \
                    /tmp/probe.yaml \"\$(cat /tmp/dca-g3-tasks/$case_name.txt)\" </dev/null
            " >"$WORK/approval-$case_name.json" 2>"$WORK/approval-$case_name.err"
            record "approval_${case_name}_exit" "$?"

            run_sbx exec "$SANDBOX" sh -c "
                printf 'file_exists=%s\n' \"\$(test -f $VM_WS/$case_name.txt && echo yes || echo no)\"
                printf 'marker_lines=%s\n' \"\$(grep -c $WRITE_MARKER $VM_WS/$case_name.txt 2>/dev/null || echo 0)\"
                printf 'ws_files=%s\n' \"\$(ls -A $VM_WS | tr '\n' ',' | sed 's/,\$//')\"
            " >"$WORK/approval-$case_name.txt" 2>"$WORK/approval-$case_name.state.err"
            record "approval_state_${case_name}_exit" "$?"
        done
    fi

    run_sbx rm --force "$SANDBOX" >"$WORK/rm-$SANDBOX.txt" 2>&1
    # Keyed by NAME, because that is what the shared cleanup rule looks for.
    record "rm_${SANDBOX}_exit" "$?"
    rm -f "$WORK/created-$SANDBOX"

    # Zero sandboxes again before the next one is created.
    run_sbx ls --json >"$WORK/ls-between-$slot.json" 2>&1
    record "ls_between_${slot}_exit" "$?"
done

scrub_credentials
record credentials_scrubbed "$([ -d "${CRED_DIR:-/nonexistent}" ] && echo no || echo yes)"

cleanup
trap - EXIT INT TERM

python3 gates/G3/record.py "$OBS" "$WORK"
