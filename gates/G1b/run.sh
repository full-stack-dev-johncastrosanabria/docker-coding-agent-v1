#!/bin/sh
# Gate G1b: Claude secret unreadability (tasks.md T021).
#
# POSIX sh. Every sbx call goes through run_sbx, which removes SSH_AUTH_SOCK first (research R14).
#
# Docker documents that the Claude subscription token "stays on your host and is never stored
# inside the sandbox" (research E8). G1a proved the practical half - a fresh sandbox authenticates
# with no /login and no API key - which means SOMETHING reaches the VM. G1b asks the security
# half: can a workload process WITH SUDO read real Claude OAuth token material?
#
# The workload deliberately has sudo, because proving only that an unprivileged user cannot read a
# secret would prove very little here: the agent already has sudo and full read access to the VM
# filesystem (E7).
#
# WHAT IS RECORDED: pattern ids, location ids, found/not-found and match counts. NEVER a token
# value, a partial value, a cookie, an Authorization header, the contents of a secret file, or a
# HASH of a candidate - a hash of a guessable secret is itself a disclosure. The scan runs inside
# the VM and emits only counts and booleans.
#
# A CANARY is planted first and must be detected, because "found nothing" is otherwise
# indistinguishable from a scanner that cannot find anything at all.
#
# One mountless sandbox, --skills off, from the exact pinned sandbox_bases.claude base, under the
# network policy accepted G4 proved for claude/trusted. Created and removed by name.

set -u

ROOT=$(CDPATH='' cd -- "$(dirname -- "$0")/../.." && pwd) || exit 1
cd "$ROOT" || exit 1

WORK=gates/G1b/work
OBS=$WORK/observations.env
KIT=gates/G6/work/kit
AGENT=gates/G1b/agent.yaml
SANDBOX=dca-g1b
AGENT_BIN=/opt/dca/bin/docker-agent
GOVERNANCE_PROBE=example.invalid:443

MARKER=DCA-G1B-OK
TASK="Reply with exactly the token $MARKER and nothing else. Do not use any tool."

record() {
    printf '%s=%s\n' "$1" "$2" >>"$OBS"
}

run_sbx() {
    env -u SSH_AUTH_SOCK sbx "$@"
}

cleanup() {
    if [ -f "$WORK/created-$SANDBOX" ]; then
        run_sbx rm --force "$SANDBOX" >"$WORK/sweep-$SANDBOX.txt" 2>&1
    fi
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
    python3 gates/G1b/record.py "$OBS" "$WORK"
    exit 1
}

case $WORK in
    gates/G1b/work) rm -rf "$WORK" || exit 1 ;;
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
    SSH_AUTH_SOCK=/nonexistent/g1b-env-probe.sock
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

python3 gates/G1b/record.py --preflight "$OBS" "$WORK" || stop

POLICY_ALLOW=$(python3 gates/G1b/record.py --policy allow) || stop
POLICY_DENY=$(python3 gates/G1b/record.py --policy deny) || stop
record policy_allow "$POLICY_ALLOW"
record policy_deny "$POLICY_DENY"

[ -d "$KIT" ] || { record kit_missing true; stop; }

trap cleanup EXIT INT TERM

# --- one mountless sandbox from the pinned Claude base ----------------------------------------------
run_sbx create claude --name "$SANDBOX" --skills off --kit "./$KIT" \
    >"$WORK/create-$SANDBOX.txt" 2>&1
create_status=$?
record "create_${SANDBOX}_exit" "$create_status"
[ "$create_status" -eq 0 ] && : >"$WORK/created-$SANDBOX"
[ "$create_status" -eq 0 ] || stop

run_sbx template ls --json >"$WORK/templates.json" 2>"$WORK/templates.err"
record templates_exit "$?"
record sbx_resolved_base "$(sed -n 's/.*\(docker\/sandbox-templates:[a-z0-9.-]*\).*/\1/p' \
    "$WORK/create-$SANDBOX.txt" | head -1)"

rules_status=0
run_sbx policy allow network --sandbox "$SANDBOX" "$POLICY_ALLOW" \
    >"$WORK/allow-$SANDBOX.txt" 2>&1 || rules_status=1
run_sbx policy deny network --sandbox "$SANDBOX" "$POLICY_DENY" \
    >"$WORK/deny-$SANDBOX.txt" 2>&1 || rules_status=1
record policy_rules_exit "$rules_status"

# --- stage the scanner and prove the agent still authenticates in THIS configuration -----------------
run_sbx cp gates/G1b/scan.py "$SANDBOX":/tmp/dca-g1b-scan.py >"$WORK/cp-scan.txt" 2>&1
record cp_scan_exit "$?"
run_sbx cp "$AGENT" "$SANDBOX":/tmp/agent.yaml >"$WORK/cp-agent.txt" 2>&1
record cp_agent_exit "$?"

# "No token is readable" is trivially true of a sandbox that cannot authenticate at all, so the
# gate re-proves authentication in the SAME configuration it then scans.
run_sbx exec "$SANDBOX" sh -c "claude auth status --json 2>/dev/null | python3 -c \"
import json,sys
try: d=json.load(sys.stdin)
except Exception: print('{}'); raise SystemExit(0)
safe=('loggedIn','authMethod','apiProvider','subscriptionType')
print(json.dumps({k:d.get(k) for k in safe}))
\"" >"$WORK/auth.json" 2>"$WORK/auth.err"
record auth_exit "$?"

run_sbx exec "$SANDBOX" sh -c \
    "cd /tmp && $AGENT_BIN run --exec --json /tmp/agent.yaml '$TASK' </dev/null" \
    >"$WORK/task.json" 2>"$WORK/task.err"
record task_exit "$?"

# --- the workload's privileges, recorded rather than assumed -------------------------------------------
run_sbx exec "$SANDBOX" sh -c "
    printf 'whoami=%s\n' \"\$(whoami)\"
    printf 'sudo_available=%s\n' \"\$(command -v sudo >/dev/null && echo yes || echo no)\"
    printf 'sudo_root=%s\n' \"\$(sudo -n whoami 2>/dev/null || echo no)\"
" >"$WORK/privs.txt" 2>"$WORK/privs.err"
record privs_exit "$?"

# --- the canary: prove the scanner can find a token when one really is there ---------------------------
# A clearly synthetic value with a real prefix. Without this control, a scanner that matched
# nothing at all would be indistinguishable from a perfectly isolated VM.
run_sbx exec "$SANDBOX" sh -c "
    umask 077
    printf 'sk-ant-oat01-DCA-G1B-CANARY-NOT-A-REAL-TOKEN\n' > /tmp/dca-g1b-canary.txt
    sudo python3 /tmp/dca-g1b-scan.py tmp
" >"$WORK/scan-canary.txt" 2>"$WORK/scan-canary.err"
record scan_canary_exit "$?"

run_sbx exec "$SANDBOX" sh -c "rm -f /tmp/dca-g1b-canary.txt" >"$WORK/canary-rm.txt" 2>&1
record canary_rm_exit "$?"

# --- the real scan, with sudo, across every location T021 names -----------------------------------------
run_sbx exec "$SANDBOX" sh -c "
    sudo python3 /tmp/dca-g1b-scan.py home etc tmp run environment proc-environ
" >"$WORK/scan.txt" 2>"$WORK/scan.err"
record scan_exit "$?"

# Where the credential actually lives, by NAME only: the point of E8 is that the token is not in
# the VM, so the gate records which candidate paths exist without reading any of them.
run_sbx exec "$SANDBOX" sh -c "
    for p in /home/agent/.claude/.credentials.json /home/agent/.claude.json \
             /home/agent/.config/claude/credentials.json /etc/claude-code/managed-settings.json; do
        printf 'path %s=%s\n' \"\$p\" \"\$(sudo test -e \"\$p\" && echo present || echo absent)\"
    done
    printf 'helper_configured=%s\n' \"\$(sudo grep -lsq apiKeyHelper /home/agent/.claude.json 2>/dev/null && echo yes || echo no)\"
" >"$WORK/paths.txt" 2>"$WORK/paths.err"
record paths_exit "$?"

# A credential file that exists but matches no pattern could mean isolation OR a token shape the
# scanner misses, and those have opposite meanings. The SHAPE is recorded - byte size and
# top-level key NAMES - so a false negative cannot hide behind an unrecognised format. Key names
# are metadata; no value is ever read, printed or hashed.
run_sbx exec "$SANDBOX" sh -c "
    sudo python3 -c \"
import json,os
for p in ('/home/agent/.claude/.credentials.json','/home/agent/.claude.json'):
    if not os.path.exists(p):
        print('shape path=%s status=absent' % p); continue
    size=os.path.getsize(p)
    try:
        d=json.load(open(p))
        keys=sorted(d.keys()) if isinstance(d,dict) else ['<not-an-object>']
    except Exception:
        keys=['<unparseable>']
    print('shape path=%s status=present bytes=%d keys=%s' % (p,size,','.join(keys)[:400]))
\"
" >"$WORK/shape.txt" 2>"$WORK/shape.err"
record shape_exit "$?"

run_sbx rm --force "$SANDBOX" >"$WORK/rm-$SANDBOX.txt" 2>&1
record "rm_${SANDBOX}_exit" "$?"
rm -f "$WORK/created-$SANDBOX"

cleanup
trap - EXIT INT TERM

python3 gates/G1b/record.py "$OBS" "$WORK"
