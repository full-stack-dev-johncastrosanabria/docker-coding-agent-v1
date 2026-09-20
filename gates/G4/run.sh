#!/bin/sh
# Gate G4: effective strict network policy, mandatory for all profiles (tasks.md T015).
#
# POSIX sh. Every sbx call goes through run_sbx, which removes SSH_AUTH_SOCK from the environment
# first (research R14).
#
# For each {claude, codex} x {trusted, untrusted} cell it creates one mountless sandbox, applies the
# profile's policy with **sandbox-scoped rules only**, and then captures the four surfaces T015
# requires, all of which record.py evaluates:
#
#   1. sbx policy ls <name> --json               the effective rule set, rule by rule
#   2. sbx policy check network --sandbox <name> <dest> --json
#   3. a real connection attempt from inside the sandbox
#   4. sbx policy log <name> --json
#
# Surface 1 is the only one that can catch a LATENT allow: 2-4 speak only about destinations this
# gate thought to probe, so none of them can see a rule for a destination no probe exercised.
#
# The matrix is not written here: `record.py --plan` derives it from the committed T014 inventory,
# so the runner and the evaluator share one definition and neither can drift from T014.
#
# GLOBAL STATE: every `sbx policy allow/deny` call passes --sandbox, so no rule G4 adds is global.
# `sbx policy init`, `sbx policy rm`, `sbx policy reset`, `sbx reset` and `sbx rm --all` are never
# called, and no setting is written. The global network-policy fingerprint is captured with ZERO
# sandboxes present, before and after, because the pinned sbx stops enumerating the global
# default-deny rule once any sandbox-scoped network rule exists.
#
# Sandboxes are created and removed ONE AT A TIME, by name, including on failure and before the
# evidence is written, so only one microVM exists at any moment.

set -u

ROOT=$(CDPATH='' cd -- "$(dirname -- "$0")/../.." && pwd) || exit 1
cd "$ROOT" || exit 1

WORK=gates/G4/work
OBS=$WORK/observations.env
PLAN=$WORK/plan.txt
KIT=gates/G6/work/kit
PORT=443
# `sbx policy check` reports the decision in its exit status (0 allowed, 1 denied) and also exits 1
# on a usage error, so the status alone proves nothing; the probe document is what record.py
# requires. The probe target is a reserved-invalid name that no policy may ever allow.
GOVERNANCE_PROBE=example.invalid:443
PREREQ=gates/G4/prereq

# A previous run's artifacts must never be read as this run's evidence. The evaluator resolves a
# missing capture to a FAIL, but a STALE one would look like a real observation, so the whole work
# directory is discarded first rather than written over file by file. Only observations.env is
# truncated by name; everything else is recreated by this run or absent.
case $WORK in
    gates/G4/work) rm -rf "$WORK" || exit 1 ;;
    *) echo "refusing to clear unexpected work dir: $WORK" >&2; exit 1 ;;
esac
mkdir -p "$WORK" || exit 1
: >"$OBS"

record() {
    printf '%s=%s\n' "$1" "$2" >>"$OBS"
}

run_sbx() {
    env -u SSH_AUTH_SOCK sbx "$@"
}

cleanup() {
    if [ -f "$PLAN" ]; then
        for name in $(awk '/^cell /{print $2}' "$PLAN"); do
            run_sbx rm --force "$name" >"$WORK/sweep-$name.txt" 2>&1
        done
    fi
    run_sbx ls --json >"$WORK/ls-after.json" 2>&1
    record ls_after_exit "$?"
    run_sbx policy ls --json >"$WORK/policy-after.json" 2>"$WORK/policy-after.err"
    record policy_after_exit "$?"
    # Raw capture, both streams and the status. record.py parses governance.active out of the
    # document in Python and requires a real boolean; nothing is scraped here, so a failed or
    # reshaped probe cannot degrade into an empty string that still hashes.
    run_sbx policy check network "$GOVERNANCE_PROBE" --json \
        >"$WORK/governance-after.json" 2>"$WORK/governance-after.err"
    record governance_after_exit "$?"
}
trap cleanup EXIT INT TERM

stop() {
    record stopped_before_sandbox true
    cleanup
    trap - EXIT INT TERM
    python3 gates/G4/record.py "$OBS" "$WORK"
    exit 1
}

# --- read-only preflight, with zero sandboxes so the fingerprint is read from a clean state ------

# What must be observed is what run_sbx passes through, not what a second `env -u` call does. A
# probe that strips SSH_AUTH_SOCK on its own line reports `removed` no matter how run_sbx is
# defined, so G4.0b's R14 clause could never fail. Instead the real wrapper is exercised against a
# stand-in named `sbx` placed first on PATH, with a sentinel socket exported so the probe bites
# even when the developer's own environment carries no agent socket. The assignments live inside
# the command substitution's subshell, so neither PATH nor SSH_AUTH_SOCK reaches any real sbx call.
mkdir -p "$WORK/envprobe" || exit 1
cat >"$WORK/envprobe/sbx" <<'STUB'
#!/bin/sh
if [ -n "${SSH_AUTH_SOCK:-}" ]; then echo present; else echo removed; fi
STUB
chmod +x "$WORK/envprobe/sbx" || exit 1
record sbx_env_ssh_auth_sock "$(
    PATH="$ROOT/$WORK/envprobe:$PATH"
    SSH_AUTH_SOCK=/nonexistent/g4-env-probe.sock
    export PATH SSH_AUTH_SOCK
    run_sbx env-probe 2>/dev/null
)"

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
run_sbx policy check network "$GOVERNANCE_PROBE" --json \
    >"$WORK/governance-before.json" 2>"$WORK/governance-before.err"
record governance_before_exit "$?"

python3 gates/G4/record.py --preflight "$OBS" "$WORK" || stop

# --- bind the run to the reviewed T014 inputs, before any sandbox exists ---------------------------
#
# The matrix is derived from these three artifacts, so the gate proves nothing about the approved
# architecture unless they are the committed, reviewed ones. Arbitrary compatible JSON - an
# uncommitted candidate added to the draft, say - would otherwise widen the trusted allow set
# silently. record.py re-checks the digests at evaluation time and refuses a mismatch.
T014_PATHS="gates/INVENTORY.json gates/inventory/control-plane-hosts.json gates/inventory/trusted-allowlist.draft.json"
t014_dirty=
for path in $T014_PATHS; do
    if ! git ls-files --error-unmatch "$path" >/dev/null 2>&1; then
        t014_dirty="$t014_dirty $path(untracked)"
    elif [ -n "$(git status --porcelain -- "$path" 2>/dev/null)" ]; then
        t014_dirty="$t014_dirty $path(modified)"
    fi
done
if [ -n "$t014_dirty" ]; then
    record t014_git_clean no
    record t014_dirty_paths "$(echo "$t014_dirty" | sed 's/^ //')"
    stop
fi
record t014_git_clean yes
python3 gates/G4/record.py --inputs >>"$OBS" 2>"$WORK/inputs.err" || { record t014_git_clean no; stop; }

[ -d "$KIT" ] || { record kit_missing true; stop; }

# A prerequisite restoration is consumed ONCE, by the first G4 run after it. Recording the digest
# here is this run's claim on it; the marker makes every later run silent, so no rerun inherits a
# restoration it did not rely on. record.py still refuses the claim unless the digest matches the
# bytes on disk AND the baseline this run started from is the one the restoration produced.
if [ -f "$PREREQ/restoration.json" ] && [ ! -f "$PREREQ/consumed-by.txt" ]; then
    digest=$(python3 -c 'import hashlib,sys; print("sha256:"+hashlib.sha256(open(sys.argv[1],"rb").read()).hexdigest())' "$PREREQ/restoration.json") || digest=
    if [ -n "$digest" ]; then
        record prereq_restoration_digest "$digest"
        printf 'consumed_by_run_at=%s\ndigest=%s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$digest" \
            >"$PREREQ/consumed-by.txt"
    fi
fi

python3 gates/G4/record.py --plan >"$PLAN" 2>"$WORK/plan.err" || { record plan_exit 1; stop; }
record plan_exit 0

# --- one cell at a time ---------------------------------------------------------------------------

for name in $(awk '/^cell /{print $2}' "$PLAN"); do
    agent=$(awk -v n="$name" '$1=="cell" && $2==n {print $5}' "$PLAN")
    allow=$(awk -v n="$name" '$1=="allow" && $2==n {print $3}' "$PLAN")
    deny=$(awk -v n="$name" '$1=="deny" && $2==n {print $3}' "$PLAN")
    dests=$(awk -v n="$name" '$1=="dest" && $2==n {print $3}' "$PLAN")

    if [ "$agent" = docker-agent ]; then
        run_sbx create "$agent" --name "$name" --skills off --kit "./$KIT" >"$WORK/create-$name.txt" 2>&1
    else
        run_sbx create "$agent" --name "$name" --skills off >"$WORK/create-$name.txt" 2>&1
    fi
    record "create_${name}_exit" "$?"

    # Sandbox-scoped rules only. A missing --sandbox here would write a GLOBAL rule.
    rules_status=0
    if [ -n "$allow" ]; then
        run_sbx policy allow network --sandbox "$name" "$allow" >"$WORK/allow-$name.txt" 2>&1 || rules_status=1
    fi
    if [ -n "$deny" ]; then
        run_sbx policy deny network --sandbox "$name" "$deny" >"$WORK/deny-$name.txt" 2>&1 || rules_status=1
    fi
    record "rules_${name}_exit" "$rules_status"

    run_sbx policy ls "$name" --json >"$WORK/effective-$name.json" 2>&1
    record "effective_${name}_exit" "$?"

    # Surface 1: the authorizer's decision for every destination.
    for host in $dests; do
        run_sbx policy check network --sandbox "$name" "$host:$PORT" --json \
            >"$WORK/check-$name-$host.json" 2>"$WORK/check-$name-$host.err"
    done

    # Surface 2: a real connection attempt for every destination, from inside the sandbox. Only the
    # proxy's CONNECT status line and the first response line are kept; no body is ever recorded.
    hostlist=$(printf '%s ' $dests)
    run_sbx exec "$name" sh -c "
        HOSTS=\"$hostlist\"
        if command -v curl >/dev/null 2>&1; then printf 'probe_tool=curl\n'; else printf 'probe_tool=none\n'; exit 0; fi
        for h in \$HOSTS; do
            printf '### host=%s\n' \"\$h\"
            curl -sS -o /dev/null -D - --max-time 20 --connect-timeout 10 \"https://\$h/\" 2>&1 | head -3
        done
    " >"$WORK/probe-$name.txt" 2>"$WORK/probe-$name.err"
    record "probe_${name}_exit" "$?"
    record "probe_tool_${name}" "$(awk -F= '/^probe_tool=/{print $2; exit}' "$WORK/probe-$name.txt")"

    # Surface 3: the decisions the proxy actually logged for this sandbox.
    run_sbx policy log "$name" --json >"$WORK/log-$name.json" 2>"$WORK/log-$name.err"
    record "log_${name}_exit" "$?"

    run_sbx rm --force "$name" >"$WORK/rm-$name.txt" 2>&1
    record "rm_${name}_exit" "$?"
done

cleanup
trap - EXIT INT TERM

python3 gates/G4/record.py "$OBS" "$WORK"
