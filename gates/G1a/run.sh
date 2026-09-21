#!/bin/sh
# Gate G1a: Claude Pro subscription execution, including fresh-sandbox authentication (T016).
#
# POSIX sh. Every sbx call goes through run_sbx, which removes SSH_AUTH_SOCK from the environment
# first (research R14).
#
# G1a is the gate that decides whether Claude exists as a V1 backend at all, and its real claim is
# about the SECOND sandbox, not the first. A backend that works only after a human has typed
# /login into that particular VM is not a backend a launcher can use. So:
#
#   step 1  dca-g1a-login  one-time interactive login, if the base is not already authenticated;
#                          a trivial non-repository task; the safe auth fields.
#   step 2  dca-g1a-fresh  a COMPLETELY FRESH sandbox, new name, no /login, stdin </dev/null, no
#                          TTY, no API key. This is what PASS depends on.
#
# Both are mountless, --skills off, from the exact pinned sandbox_bases.claude base, with the G6
# kit and the network policy accepted G4 proved for claude/trusted. No repository code is ever
# placed in either sandbox.
#
# CREDENTIALS: `claude auth status --json` also carries email, orgId and orgName. The raw document
# is NEVER written to the work directory. It is projected INSIDE the VM down to the four safe
# fields T016 names (loggedIn, authMethod, apiProvider, subscriptionType), so nothing else leaves
# the sandbox. No token, cookie, header or auth file is read by this gate at any point.
#
# GLOBAL STATE: every `sbx policy allow/deny` call passes --sandbox, so no rule this gate adds is
# global. `sbx policy init`, `sbx policy rm`, `sbx policy reset`, `sbx reset` and `sbx rm --all`
# are never called and no setting is written. The global fingerprint is captured with zero
# sandboxes present, before and after.
#
# Sandboxes are created and removed ONE AT A TIME, by name, including on failure.
#
# Phases, because step 1 may need a human:
#   sh gates/G1a/run.sh login    preflight, create the login sandbox, report whether it is
#                                already authenticated (the pinned base may inherit host-side
#                                credentials, in which case no /login is needed at all)
#   sh gates/G1a/run.sh check    re-read the safe auth fields after the developer ran /login
#   sh gates/G1a/run.sh run      step 1 task, then the fresh step-2 sandbox, cleanup, evidence

set -u

ROOT=$(CDPATH='' cd -- "$(dirname -- "$0")/../.." && pwd) || exit 1
cd "$ROOT" || exit 1

WORK=gates/G1a/work
OBS=$WORK/observations.env
KIT=gates/G6/work/kit
AGENT=gates/G1a/agent.yaml
LOGIN_SANDBOX=dca-g1a-login
FRESH_SANDBOX=dca-g1a-fresh
MARKER=DCA-G1A-OK
TASK="Reply with exactly the token $MARKER and nothing else. Do not use any tool."
GOVERNANCE_PROBE=example.invalid:443
# Provider API-key names checked for ABSENCE by name only. A value is never read or recorded.
API_KEY_NAMES="ANTHROPIC_API_KEY ANTHROPIC_AUTH_TOKEN CLAUDE_API_KEY OPENAI_API_KEY"
# The pinned Docker Agent artifact the G6 kit installs. `docker agent` is NOT a command inside
# the sandbox, so the trusted absolute path is what the gate runs.
AGENT_BIN=/opt/dca/bin/docker-agent

PHASE=${1:-run}

record() {
    printf '%s=%s\n' "$1" "$2" >>"$OBS"
}

run_sbx() {
    env -u SSH_AUTH_SOCK sbx "$@"
}

# The safe projection. `claude auth status --json` is parsed INSIDE the VM and only the four
# fields T016 names are emitted, so email, orgId and orgName never reach the host work directory.
AUTH_PROJECTION='claude auth status --json 2>/dev/null | python3 -c "
import json,sys
try:
    d=json.load(sys.stdin)
except Exception:
    print(\"{}\"); raise SystemExit(0)
safe=(\"loggedIn\",\"authMethod\",\"apiProvider\",\"subscriptionType\")
print(json.dumps({k:d.get(k) for k in safe}))
"'

# What the VM actually ships. The base is pinned by digest, but the CLI versions inside it are
# a separate fact, and a gate that claims Claude executed should say which Claude did.
capture_versions() {
    run_sbx exec "$1" sh -c '
        printf "claude_code=%s\n" "$(claude --version 2>/dev/null | awk "{print \$1}")"
        printf "docker_agent=%s\n" "$(/opt/dca/bin/docker-agent version 2>/dev/null | awk "/^docker-agent version/{print \$3}")"
    ' >"$WORK/versions-$1.txt" 2>"$WORK/versions-$1.err"
    record "versions_$1_exit" "$?"
}

# $1 = sandbox, $2 = optional label. Step 2 reads the safe fields BOTH before and after its task:
# before proves the fresh sandbox was already authenticated without any /login, and after is the
# settled state once the CLI has actually talked to the service. Recording both means the gate
# reports when each field becomes available instead of guessing.
capture_auth() {
    label=${2:-$1}
    run_sbx exec "$1" sh -c "$AUTH_PROJECTION" \
        >"$WORK/auth-$label.json" 2>"$WORK/auth-$label.err"
    record "auth_${label}_exit" "$?"
}

cleanup() {
    for name in "$LOGIN_SANDBOX" "$FRESH_SANDBOX"; do
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
    python3 gates/G1a/record.py "$OBS" "$WORK"
    exit 1
}

# --- apply the accepted G4 claude/trusted policy to one named sandbox ------------------------------
# $1 = sandbox name, $2 = trusted|login. The LOGIN set exists only for the one-time, non-repository
# login sandbox: it adds the documented host-oauth-login destinations and drops the trusted
# profile's denies on exactly those hosts. It is never applied to the step-2 sandbox, and G1a
# proves that separately by checking those hosts are denied there.
apply_policy() {
    name=$1
    case ${2:-trusted} in
        login) allow=$POLICY_LOGIN_ALLOW; deny=$POLICY_LOGIN_DENY ;;
        *)     allow=$POLICY_ALLOW;       deny=$POLICY_DENY ;;
    esac
    status=0
    if [ -n "$allow" ]; then
        run_sbx policy allow network --sandbox "$name" "$allow" \
            >"$WORK/allow-$name.txt" 2>&1 || status=1
    fi
    if [ -n "$deny" ]; then
        run_sbx policy deny network --sandbox "$name" "$deny" \
            >"$WORK/deny-$name.txt" 2>&1 || status=1
    fi
    run_sbx policy ls "$name" --json >"$WORK/effective-$name.json" 2>&1
    record "effective_${name}_exit" "$?"
    return $status
}

load_policy() {
    POLICY_ALLOW=$(python3 gates/G1a/record.py --policy allow) || return 1
    POLICY_DENY=$(python3 gates/G1a/record.py --policy deny) || return 1
    POLICY_LOGIN_ALLOW=$(python3 gates/G1a/record.py --policy login-allow) || return 1
    POLICY_LOGIN_DENY=$(python3 gates/G1a/record.py --policy login-deny) || return 1
    POLICY_LOGIN_HOSTS=$(python3 gates/G1a/record.py --policy login-hosts) || return 1
    return 0
}

# ==================================================================================================
case $PHASE in
login)
    # A previous run's artifacts must never be read as this run's evidence, so the whole work
    # directory is discarded rather than written over file by file.
    case $WORK in
        gates/G1a/work) rm -rf "$WORK" || exit 1 ;;
        *) echo "refusing to clear unexpected work dir: $WORK" >&2; exit 1 ;;
    esac
    mkdir -p "$WORK" || exit 1
    : >"$OBS"

    # --- read-only preflight, with zero sandboxes -------------------------------------------------
    mkdir -p "$WORK/envprobe" || exit 1
    cat >"$WORK/envprobe/sbx" <<'STUB'
#!/bin/sh
if [ -n "${SSH_AUTH_SOCK:-}" ]; then echo present; else echo removed; fi
STUB
    chmod +x "$WORK/envprobe/sbx" || exit 1
    # Measures run_sbx itself against a stand-in named sbx, with a sentinel socket, so the
    # observation cannot report `removed` merely because the probe stripped the variable.
    record sbx_env_ssh_auth_sock "$(
        PATH="$ROOT/$WORK/envprobe:$PATH"
        SSH_AUTH_SOCK=/nonexistent/g1a-env-probe.sock
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

    python3 gates/G1a/record.py --preflight "$OBS" "$WORK" || stop

    load_policy || { record policy_load_failed true; stop; }
    record policy_allow "$POLICY_ALLOW"
    record policy_deny "$POLICY_DENY"
    record policy_login_allow "$POLICY_LOGIN_ALLOW"
    record policy_login_deny "$POLICY_LOGIN_DENY"
    record policy_login_hosts "$POLICY_LOGIN_HOSTS"

    [ -d "$KIT" ] || { record kit_missing true; stop; }

    trap cleanup EXIT INT TERM

    # --- step 1: the login sandbox ----------------------------------------------------------------
    run_sbx create claude --name "$LOGIN_SANDBOX" --skills off --kit "./$KIT" \
        >"$WORK/create-$LOGIN_SANDBOX.txt" 2>&1
    create_status=$?
    record "create_${LOGIN_SANDBOX}_exit" "$create_status"
    [ "$create_status" -eq 0 ] && : >"$WORK/created-$LOGIN_SANDBOX"
    [ "$create_status" -eq 0 ] || stop

    run_sbx template ls --json >"$WORK/templates.json" 2>"$WORK/templates.err"
    record templates_exit "$?"
    record sbx_resolved_base "$(awk '/resolved|image/ && /sandbox-templates/ {print; exit}' \
        "$WORK/create-$LOGIN_SANDBOX.txt" | sed 's/.*\(docker\/sandbox-templates:[a-z0-9.-]*\).*/\1/')"

    apply_policy "$LOGIN_SANDBOX" login
    record policy_rules_exit "$?"

    capture_auth "$LOGIN_SANDBOX"

    trap - EXIT INT TERM
    printf '\n'
    if python3 gates/G1a/record.py --proves-pro "$WORK/auth-$LOGIN_SANDBOX.json"; then
        echo "G1a step 1: $LOGIN_SANDBOX proves the PRO subscription. Next: sh gates/G1a/run.sh run"
    else
        echo "G1a step 1: $LOGIN_SANDBOX does not yet prove the PRO subscription."
        echo "  (a sandbox that merely INHERITED the host credential reports subscriptionType"
        echo "   null, so step 1 needs the interactive login to be performed in this VM.)"
        echo "The developer must complete the Claude Pro login inside this sandbox:"
        echo "    sbx run --name $LOGIN_SANDBOX"
        echo "  then, inside Claude Code, type:  /login"
        echo "Then: sh gates/G1a/run.sh check"
    fi
    ;;

check)
    [ -f "$OBS" ] || { echo "run 'sh gates/G1a/run.sh login' first" >&2; exit 1; }
    capture_auth "$LOGIN_SANDBOX"
    if python3 gates/G1a/record.py --proves-pro "$WORK/auth-$LOGIN_SANDBOX.json"; then
        echo "G1a: $LOGIN_SANDBOX proves the PRO subscription. Next: sh gates/G1a/run.sh run"
    else
        echo "G1a: $LOGIN_SANDBOX still does not prove the PRO subscription." >&2
        exit 1
    fi
    ;;

run)
    [ -f "$OBS" ] || { echo "run 'sh gates/G1a/run.sh login' first" >&2; exit 1; }
    load_policy || { record policy_load_failed true; stop; }
    trap cleanup EXIT INT TERM

    # --- step 1: the trivial non-repository task, in the authenticated login sandbox --------------
    capture_auth "$LOGIN_SANDBOX"
    run_sbx cp "$AGENT" "$LOGIN_SANDBOX":/tmp/agent.yaml >"$WORK/cp-$LOGIN_SANDBOX.txt" 2>&1
    record "cp_${LOGIN_SANDBOX}_exit" "$?"
    run_sbx exec "$LOGIN_SANDBOX" sh -c \
        "cd /tmp && $AGENT_BIN run --exec --json /tmp/agent.yaml '$TASK' </dev/null" \
        >"$WORK/task-$LOGIN_SANDBOX.json" 2>"$WORK/task-$LOGIN_SANDBOX.err"
    record "task_${LOGIN_SANDBOX}_exit" "$?"

    run_sbx rm --force "$LOGIN_SANDBOX" >"$WORK/rm-$LOGIN_SANDBOX.txt" 2>&1
    record "rm_${LOGIN_SANDBOX}_exit" "$?"
    rm -f "$WORK/created-$LOGIN_SANDBOX"

    # --- step 2: a completely fresh sandbox, no /login, no TTY, no API key -------------------------
    run_sbx create claude --name "$FRESH_SANDBOX" --skills off --kit "./$KIT" \
        >"$WORK/create-$FRESH_SANDBOX.txt" 2>&1
    create_status=$?
    record "create_${FRESH_SANDBOX}_exit" "$create_status"
    [ "$create_status" -eq 0 ] && : >"$WORK/created-$FRESH_SANDBOX"
    [ "$create_status" -eq 0 ] || stop

    apply_policy "$FRESH_SANDBOX" trusted
    record "policy_rules_fresh_exit" "$?"

    # The login allowance must NOT have escaped the login sandbox. Every documented
    # host-oauth-login destination is asked for explicitly here and must be denied.
    for host in $(printf '%s' "$POLICY_LOGIN_HOSTS" | tr ',' ' '); do
        run_sbx policy check network --sandbox "$FRESH_SANDBOX" "$host:443" --json \
            >"$WORK/check-$FRESH_SANDBOX-$host.json" 2>"$WORK/check-$FRESH_SANDBOX-$host.err"
    done

    # Presence-by-name only: the VM is asked which of these names are SET, never for a value.
    run_sbx exec "$FRESH_SANDBOX" sh -c "
        for n in $API_KEY_NAMES; do
            eval \"v=\\\${\$n:-}\"
            if [ -n \"\$v\" ]; then printf '%s=present\n' \"\$n\"; else printf '%s=absent\n' \"\$n\"; fi
        done
    " >"$WORK/apikeys-$FRESH_SANDBOX.txt" 2>"$WORK/apikeys-$FRESH_SANDBOX.err"
    record "apikeys_${FRESH_SANDBOX}_exit" "$?"

    capture_versions "$FRESH_SANDBOX"
    capture_auth "$FRESH_SANDBOX" "$FRESH_SANDBOX-pre"

    run_sbx cp "$AGENT" "$FRESH_SANDBOX":/tmp/agent.yaml >"$WORK/cp-$FRESH_SANDBOX.txt" 2>&1
    record "cp_${FRESH_SANDBOX}_exit" "$?"
    # Headless: stdin from /dev/null and no TTY (sbx exec allocates none), --exec so Docker Agent
    # runs without a TUI and rejects every confirmation request.
    run_sbx exec "$FRESH_SANDBOX" sh -c \
        "cd /tmp && $AGENT_BIN run --exec --json /tmp/agent.yaml '$TASK' </dev/null" \
        >"$WORK/task-$FRESH_SANDBOX.json" 2>"$WORK/task-$FRESH_SANDBOX.err"
    record "task_${FRESH_SANDBOX}_exit" "$?"

    capture_auth "$FRESH_SANDBOX" "$FRESH_SANDBOX-post"

    run_sbx policy log "$FRESH_SANDBOX" --json >"$WORK/log-$FRESH_SANDBOX.json" 2>&1
    record "log_${FRESH_SANDBOX}_exit" "$?"

    run_sbx rm --force "$FRESH_SANDBOX" >"$WORK/rm-$FRESH_SANDBOX.txt" 2>&1
    record "rm_${FRESH_SANDBOX}_exit" "$?"
    rm -f "$WORK/created-$FRESH_SANDBOX"

    cleanup
    trap - EXIT INT TERM

    python3 gates/G1a/record.py "$OBS" "$WORK"
    ;;

*)
    echo "usage: sh gates/G1a/run.sh [login|check|run]" >&2
    exit 2
    ;;
esac
