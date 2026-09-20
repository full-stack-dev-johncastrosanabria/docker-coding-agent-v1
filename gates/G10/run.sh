#!/bin/sh
# Gate G10: sanitized source delivery (tasks.md T012). Two independent properties, in order:
#
#   Phase A  the dirty-tree preflight fails closed with no override: an uncommitted tracked edit
#            or an untracked non-ignored file refuses the run before any bundle or sandbox
#            exists. Ignored files alone never trigger it (proven by a control).
#   Phase B  under the explicit gate-local --ignore-uncommitted equivalent, only the selected
#            committed branch state reaches a mountless sandbox.
#
# Neither may be weakened for the other. Phase A mirrors launcher-cli precondition 2 with the
# smallest gate-local observation (`git status --porcelain`, which excludes ignored files by
# design); it does not implement the launcher or its ref parser (T064).
#
# POSIX sh. Every sbx call goes through run_sbx, which removes SSH_AUTH_SOCK first (R14).
# Nothing global changes: no settings, no policy, no sbx reset, no sbx rm --all. The one
# sandbox is mountless, named, and removed by name, including on failure and before FAIL
# evidence is written. Canary values live only in the git-ignored fixture; the evidence records
# canary IDs and match counts, never values or file contents.

set -u

ROOT=$(CDPATH='' cd -- "$(dirname -- "$0")/../.." && pwd) || exit 1
cd "$ROOT" || exit 1

WORK=gates/G10/work
OBS=$WORK/observations.env
FIXTURE=$WORK/fixture
BUNDLE=$WORK/src.bundle
SANDBOX=dca-g10-source
SELECTED=dca-g10-selected
SECOND=dca-g10-second

rm -rf "$WORK"
mkdir -p "$WORK" || exit 1
: >"$OBS"

record() {
    printf '%s=%s\n' "$1" "$2" >>"$OBS"
}

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
    record stopped true
    cleanup
    trap - EXIT INT TERM
    python3 gates/G10/record.py "$OBS" "$WORK"
    exit 1
}

# --- read-only preflight -----------------------------------------------------------------------

record sbx_env_ssh_auth_sock "$(env -u SSH_AUTH_SOCK sh -c 'if [ -n "${SSH_AUTH_SOCK:-}" ]; then echo present; else echo removed; fi')"

run_sbx version --json >"$WORK/pf-version.json" 2>"$WORK/pf-version.err"
record pf_version_exit "$?"
run_sbx settings get --json ssh.agentForwardingEnabled >"$WORK/pf-ssh-forwarding.json" 2>&1
record pf_ssh_forwarding_exit "$?"
run_sbx settings get --json ssh.agentSocketPath >"$WORK/pf-ssh-socket.json" 2>&1
record pf_ssh_socket_exit "$?"
run_sbx policy ls --json >"$WORK/pf-policy.json" 2>"$WORK/pf-policy.err"
record pf_policy_exit "$?"
run_sbx template ls --json >"$WORK/templates.json" 2>"$WORK/templates.err"
record template_ls_exit "$?"
# The zero-sandbox baseline: Phase A's refusal is compared against exactly this listing.
run_sbx ls --json >"$WORK/ls-baseline.json" 2>"$WORK/ls-baseline.err"
record pf_ls_exit "$?"

python3 gates/G10/record.py --preflight "$OBS" "$WORK" || stop

# --- fixture ------------------------------------------------------------------------------------
# Canary values are random and stay in the git-ignored fixture; only their IDs reach the evidence.

canary() { LC_ALL=C tr -dc 'a-f0-9' </dev/urandom | head -c 32; }
BASELINE_VALUE=$(canary)
ENV_VALUE=$(canary)
UNTRACKED_VALUE=$(canary)
DIRTY_VALUE=$(canary)
SECOND_VALUE=$(canary)
record canary_ids "baseline env-ignored untracked dirty-edit second-branch"

mkdir -p "$FIXTURE" || exit 1
git -C "$FIXTURE" init -q -b "$SELECTED" >/dev/null 2>&1
git -C "$FIXTURE" config user.email gate@example.invalid
git -C "$FIXTURE" config user.name "G10 fixture"
printf '.env\n' >"$FIXTURE/.gitignore"
printf 'baseline canary %s\n' "$BASELINE_VALUE" >"$FIXTURE/tracked.txt"
git -C "$FIXTURE" add .gitignore tracked.txt >/dev/null 2>&1
git -C "$FIXTURE" commit -q -m "baseline" >/dev/null 2>&1
record fixture_baseline_commit "$(git -C "$FIXTURE" rev-parse HEAD)"

# Second branch: a commit unique to it, not reachable from the selected branch.
git -C "$FIXTURE" checkout -q -b "$SECOND" >/dev/null 2>&1
printf 'branch-only canary %s\n' "$SECOND_VALUE" >"$FIXTURE/branch-only.txt"
git -C "$FIXTURE" add branch-only.txt >/dev/null 2>&1
git -C "$FIXTURE" commit -q -m "second branch only" >/dev/null 2>&1
record second_branch_commit "$(git -C "$FIXTURE" rev-parse HEAD)"
git -C "$FIXTURE" checkout -q "$SELECTED" >/dev/null 2>&1

# A commit on the selected branch after the fork, so the branches genuinely diverge.
printf 'selected canary %s\n' "$BASELINE_VALUE" >>"$FIXTURE/tracked.txt"
git -C "$FIXTURE" commit -q -am "selected branch commit" >/dev/null 2>&1
record selected_commit "$(git -C "$FIXTURE" rev-parse HEAD)"
record second_is_ancestor "$(git -C "$FIXTURE" merge-base --is-ancestor "$(git -C "$FIXTURE" rev-parse "$SECOND")" HEAD >/dev/null 2>&1 && echo yes || echo no)"

plant_dirty() {
    printf 'ignored canary %s\n' "$ENV_VALUE" >"$FIXTURE/.env"
    printf 'untracked canary %s\n' "$UNTRACKED_VALUE" >"$FIXTURE/untracked-canary.txt"
    printf 'uncommitted edit canary %s\n' "$DIRTY_VALUE" >>"$FIXTURE/tracked.txt"
}
plant_dirty

# --- Phase A: the dirty-tree decision, case by case -------------------------------------------
# gates/G10/dirty_tree.sh decides; this loop puts the fixture into each state and records the
# decision, its exit code, and — for every REFUSE — that nothing was created by that path.

clean_tree() {
    git -C "$FIXTURE" checkout -q -- tracked.txt 2>/dev/null
    rm -f "$FIXTURE/untracked-canary.txt" "$FIXTURE/.env"
}

decide() {
    # $1 = case name. Records the decision, and for a refusal the absence of side effects.
    out=$(sh gates/G10/dirty_tree.sh "$FIXTURE" 2>"$WORK/decide-$1.err")
    exit_code=$?
    printf '%s\n' "$out" >"$WORK/decide-$1.txt"
    record "decide_${1}_exit" "$exit_code"
    record "decide_${1}_decision" "$(printf '%s\n' "$out" | sed -n 's/^decision=\([a-z]*\)$/\1/p')"
    record "decide_${1}_tracked" "$(printf '%s\n' "$out" | sed -n 's/^tracked_changes=\([0-9]*\)$/\1/p')"
    record "decide_${1}_untracked" "$(printf '%s\n' "$out" | sed -n 's/^untracked_files=\([0-9]*\)$/\1/p')"
    record "decide_${1}_bundle_exists" "$(test -e "$BUNDLE" && echo yes || echo no)"
    run_sbx ls --json >"$WORK/ls-decide-$1.json" 2>&1
    record "decide_${1}_ls_exit" "$?"
}

# tracked-only
clean_tree
printf 'uncommitted edit canary %s\n' "$DIRTY_VALUE" >>"$FIXTURE/tracked.txt"
decide tracked_only

# untracked-only
clean_tree
printf 'untracked canary %s\n' "$UNTRACKED_VALUE" >"$FIXTURE/untracked-canary.txt"
decide untracked_only

# tracked + untracked
printf 'uncommitted edit canary %s\n' "$DIRTY_VALUE" >>"$FIXTURE/tracked.txt"
decide both

# ignored-only: an ignored .env must never refuse a run
clean_tree
printf 'ignored canary %s\n' "$ENV_VALUE" >"$FIXTURE/.env"
record ignored_only_env_present "$(test -e "$FIXTURE/.env" && echo yes || echo no)"
record ignored_only_env_is_ignored "$(git -C "$FIXTURE" check-ignore -q .env && echo yes || echo no)"
decide ignored_only

# clean
clean_tree
decide clean

# Phase B runs against a dirty tree under the explicit override, so plant everything again and
# record the state it actually starts from.
plant_dirty
git -C "$FIXTURE" status --porcelain=v1 >"$WORK/status-before-phase-b.txt" 2>&1
record status_before_phase_b_exit "$?"
record phase_b_tracked "$(awk 'NF && !/^\?\?/ {c++} END {print c+0}' "$WORK/status-before-phase-b.txt")"
record phase_b_untracked "$(awk '/^\?\?/ {c++} END {print c+0}' "$WORK/status-before-phase-b.txt")"
record phase_b_mentions_ignored "$(grep -c '\.env' "$WORK/status-before-phase-b.txt" 2>/dev/null || echo 0)"

python3 gates/G10/record.py --phase-a "$OBS" "$WORK" || stop

# --- Phase B: explicit override, committed-state delivery --------------------------------------
# Recorded explicitly: this branch of the gate exercises the approved --ignore-uncommitted path.

record override_ignore_uncommitted "gate-local --ignore-uncommitted equivalent, explicitly exercised"
record selected_ref "$(git -C "$FIXTURE" symbolic-ref --quiet HEAD)"
record selected_ref_commit "$(git -C "$FIXTURE" rev-parse "refs/heads/$SELECTED")"

# The named ref, never raw HEAD.
git -C "$FIXTURE" bundle create "$(cd "$ROOT" && pwd)/$BUNDLE" "refs/heads/$SELECTED" >"$WORK/bundle-create.txt" 2>&1
record bundle_create_exit "$?"
git -C "$FIXTURE" bundle verify "$(cd "$ROOT" && pwd)/$BUNDLE" >"$WORK/bundle-verify.txt" 2>&1
record bundle_verify_exit "$?"
git -C "$FIXTURE" bundle list-heads "$(cd "$ROOT" && pwd)/$BUNDLE" >"$WORK/bundle-heads.txt" 2>&1
record bundle_heads_exit "$?"
record bundle_head_count "$(grep -c . "$WORK/bundle-heads.txt" 2>/dev/null || echo 0)"
record bundle_head_line "$(head -n 1 "$WORK/bundle-heads.txt" 2>/dev/null)"

run_sbx create claude --name "$SANDBOX" --skills off >"$WORK/create-$SANDBOX.txt" 2>&1
record "create_${SANDBOX}_exit" "$?"
record "image_${SANDBOX}" "$(awk '/^ *image  */ {print $2; exit}' "$WORK/create-$SANDBOX.txt")"
record "workspace_line_${SANDBOX}" "$(awk '/^ *workspace  */ {$1=""; print; exit}' "$WORK/create-$SANDBOX.txt" | sed 's/^ *//')"

run_sbx cp "$BUNDLE" "$SANDBOX:/home/agent/src.bundle" >"$WORK/cp.txt" 2>&1
record cp_exit "$?"

# In-VM: clone the bundle, then prove what did and didn't arrive. Canary values are passed as
# arguments so the VM can search for them; only counts come back.
run_sbx exec "$SANDBOX" sh -c '
    set -u
    baseline=$1; envc=$2; untracked=$3; dirty=$4; second=$5; second_sha=$6; selected_sha=$7
    rm -rf /home/agent/workspace/repo
    mkdir -p /home/agent/workspace
    git clone -q /home/agent/src.bundle /home/agent/workspace/repo 2>/dev/null
    printf "clone_exit=%s\n" "$?"
    # A bundle of a named ref carries no HEAD, so the clone has no checkout. R11 has the VM
    # create the run branch at source.commit; the gate mirrors that with the selected commit.
    git -C /home/agent/workspace/repo checkout -q -B dca/g10-run "$selected_sha" 2>/dev/null
    printf "checkout_exit=%s\n" "$?"
    printf "source_mount_path_exists=%s\n" "$(test -e /run/sandbox/source && echo yes || echo no)"
    printf "workspace_mounts=%s\n" "$(awk "\$2 ~ /workspace|sandbox\\/source/ {c++} END {print c+0}" /proc/mounts)"
    printf "host_fs_mount_targets=%s\n" "$(awk "\$3 ~ /^(virtiofs|9p|nfs)\$/ {printf \"%s \", \$2}" /proc/mounts)"
    printf "mount_total=%s\n" "$(wc -l < /proc/mounts | tr -d " ")"
    cd /home/agent/workspace/repo 2>/dev/null || exit 1
    printf "head_commit=%s\n" "$(git rev-parse HEAD 2>/dev/null)"
    printf "selected_commit_present=%s\n" "$(git cat-file -e "$selected_sha^{commit}" 2>/dev/null && echo yes || echo no)"
    printf "second_commit_present=%s\n" "$(git cat-file -e "$second_sha^{commit}" 2>/dev/null && echo yes || echo no)"
    printf "second_ref_hits=%s\n" "$(git show-ref 2>/dev/null | grep -c "dca-g10-second" || true)"
    printf "second_in_rev_list=%s\n" "$(git rev-list --all 2>/dev/null | grep -c "^$second_sha$" || true)"
    printf "ref_names=%s\n" "$(git show-ref 2>/dev/null | awk "{printf \"%s \", \$2}")"
    printf "baseline_canary_hits=%s\n" "$(grep -rl "$baseline" /home/agent/workspace/repo 2>/dev/null | wc -l | tr -d " ")"
    printf "env_canary_hits=%s\n" "$(grep -rl "$envc" /home/agent 2>/dev/null | wc -l | tr -d " ")"
    printf "untracked_canary_hits=%s\n" "$(grep -rl "$untracked" /home/agent 2>/dev/null | wc -l | tr -d " ")"
    printf "dirty_canary_hits=%s\n" "$(grep -rl "$dirty" /home/agent 2>/dev/null | wc -l | tr -d " ")"
    printf "second_canary_hits=%s\n" "$(grep -rl "$second" /home/agent 2>/dev/null | wc -l | tr -d " ")"
    printf "env_file_present=%s\n" "$(test -e /home/agent/workspace/repo/.env && echo yes || echo no)"
    printf "untracked_file_present=%s\n" "$(test -e /home/agent/workspace/repo/untracked-canary.txt && echo yes || echo no)"
    printf "branch_only_file_present=%s\n" "$(test -e /home/agent/workspace/repo/branch-only.txt && echo yes || echo no)"
    printf "probe_complete=yes\n"
' sh "$BASELINE_VALUE" "$ENV_VALUE" "$UNTRACKED_VALUE" "$DIRTY_VALUE" "$SECOND_VALUE" \
    "$(git -C "$FIXTURE" rev-parse "refs/heads/$SECOND")" "$(git -C "$FIXTURE" rev-parse "refs/heads/$SELECTED")" \
    >"$WORK/probe-$SANDBOX.env" 2>"$WORK/probe-$SANDBOX.err"
record "probe_${SANDBOX}_exit" "$?"

cleanup
trap - EXIT INT TERM

python3 gates/G10/record.py "$OBS" "$WORK"
