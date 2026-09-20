#!/bin/sh
# Gate G5: bundle round-trip, retrieval and disposal (tasks.md T013), the reverse half of R11.
#
# G10 proved sanitized committed source going INTO a mountless VM. G5 proves the way back:
# a task commit on dca/<run-id> leaves the VM as a bundle, reaches a host quarantine directory
# via sbx cp, is verified and identity-checked there, and only then is fetched as exactly that
# one ref — with the host working tree, index and every other ref byte-identical, no checkout
# or merge, and the VM removed. Two negatives run separately: a corrupted returned bundle, and
# a genuine retrieval copy failure. Neither may leave a ref behind.
#
# This is a gate-local architectural proof. T070/T071 own production retrieval and
# finalization; nothing here is launcher code.
#
# POSIX sh. Every sbx call goes through run_sbx, which removes SSH_AUTH_SOCK first (R14).
# Nothing global changes: no settings, no policy, no sbx reset, no sbx rm --all. The one
# sandbox is mountless, named, and removed by name, including on failure and before FAIL
# evidence is written. The "host" repository is the gate fixture under gates/G5/work/, never
# this project's repository.

set -u

ROOT=$(CDPATH='' cd -- "$(dirname -- "$0")/../.." && pwd) || exit 1
cd "$ROOT" || exit 1

WORK=gates/G5/work
OBS=$WORK/observations.env
FIXTURE=$WORK/fixture
QUARANTINE=$WORK/quarantine
INPUT_BUNDLE=$WORK/src.bundle
SANDBOX=dca-g5-roundtrip
SELECTED=dca-g5-selected
RUN_ID=g5-happy
NEG_A=g5-corrupt
NEG_B=g5-copyfail

rm -rf "$WORK"
mkdir -p "$WORK" "$QUARANTINE" || exit 1
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
    python3 gates/G5/record.py "$OBS" "$WORK"
    exit 1
}

# Deterministic snapshot of the host fixture: HEAD, attachment, every ref, tree/index state and
# the bytes of every tracked file. record.py compares two snapshots for equality.
snapshot() {
    label=$1
    git -C "$FIXTURE" rev-parse HEAD >"$WORK/snap-$label-head.txt" 2>&1
    git -C "$FIXTURE" symbolic-ref --quiet HEAD >"$WORK/snap-$label-symref.txt" 2>&1 || \
        printf 'DETACHED\n' >"$WORK/snap-$label-symref.txt"
    git -C "$FIXTURE" for-each-ref --format='%(objectname) %(refname)' >"$WORK/snap-$label-refs.txt" 2>&1
    git -C "$FIXTURE" status --porcelain=v1 >"$WORK/snap-$label-status.txt" 2>&1
    shasum -a 256 "$FIXTURE/.git/index" 2>/dev/null | awk '{print $1}' >"$WORK/snap-$label-index.txt"
    (cd "$FIXTURE" && git ls-files -z | xargs -0 shasum -a 256 2>/dev/null | sort) \
        >"$WORK/snap-$label-files.txt" 2>&1
    record "snapshot_${label}_head" "$(cat "$WORK/snap-$label-head.txt")"
    record "snapshot_${label}_refs" "$(grep -c . "$WORK/snap-$label-refs.txt" 2>/dev/null || echo 0)"
}

# --- read-only preflight -------------------------------------------------------------------------

record sbx_env_ssh_auth_sock "$(env -u SSH_AUTH_SOCK sh -c 'if [ -n "${SSH_AUTH_SOCK:-}" ]; then echo present; else echo removed; fi')"
record run_id "$RUN_ID"
record negative_a_run_id "$NEG_A"
record negative_b_run_id "$NEG_B"

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
run_sbx ls --json >"$WORK/ls-baseline.json" 2>"$WORK/ls-baseline.err"
record pf_ls_exit "$?"

python3 gates/G5/record.py --preflight "$OBS" "$WORK" || stop

# --- host fixture and the verified input bundle (the G10 path) ------------------------------------

mkdir -p "$FIXTURE" || exit 1
git -C "$FIXTURE" init -q -b "$SELECTED" >/dev/null 2>&1
git -C "$FIXTURE" config user.email gate@example.invalid
git -C "$FIXTURE" config user.name "G5 fixture"
git -C "$FIXTURE" config core.hooksPath /dev/null
printf 'baseline\n' >"$FIXTURE/tracked.txt"
git -C "$FIXTURE" add tracked.txt >/dev/null 2>&1
git -C "$FIXTURE" commit -q -m "baseline" >/dev/null 2>&1
record source_commit "$(git -C "$FIXTURE" rev-parse "refs/heads/$SELECTED")"
record selected_ref "$(git -C "$FIXTURE" symbolic-ref --quiet HEAD)"

git -C "$FIXTURE" bundle create "$ROOT/$INPUT_BUNDLE" "refs/heads/$SELECTED" >"$WORK/input-bundle-create.txt" 2>&1
record input_bundle_create_exit "$?"
git -C "$FIXTURE" bundle verify "$ROOT/$INPUT_BUNDLE" >"$WORK/input-bundle-verify.txt" 2>&1
record input_bundle_verify_exit "$?"

# --- the sandbox and the task commit ---------------------------------------------------------------

run_sbx create claude --name "$SANDBOX" --skills off >"$WORK/create-$SANDBOX.txt" 2>&1
record "create_${SANDBOX}_exit" "$?"
record "image_${SANDBOX}" "$(awk '/^ *image  */ {print $2; exit}' "$WORK/create-$SANDBOX.txt")"
record "workspace_line_${SANDBOX}" "$(awk '/^ *workspace  */ {$1=""; print; exit}' "$WORK/create-$SANDBOX.txt" | sed 's/^ *//')"

run_sbx cp "$INPUT_BUNDLE" "$SANDBOX:/home/agent/src.bundle" >"$WORK/cp-in.txt" 2>&1
record cp_in_exit "$?"

# In the VM: clone, create the task branch explicitly at source.commit (a bundle of a named ref
# carries no HEAD), disable hooks per R11, commit a deterministic candidate change, and bundle
# exactly that ref. Two more task branches are prepared for the negatives.
run_sbx exec "$SANDBOX" sh -c '
    set -u
    source_sha=$1; run_id=$2; neg_a=$3; neg_b=$4
    repo=/home/agent/workspace/repo
    rm -rf "$repo"; mkdir -p /home/agent/workspace
    git clone -q /home/agent/src.bundle "$repo" 2>/dev/null
    printf "clone_exit=%s\n" "$?"
    cd "$repo" || exit 1
    git config core.hooksPath /dev/null
    printf "hooks_path=%s\n" "$(git config --get core.hooksPath)"
    git config user.email gate@example.invalid
    git config user.name "G5 sandbox"
    git checkout -q -B "dca/$run_id" "$source_sha" 2>/dev/null
    printf "task_branch_exit=%s\n" "$?"
    printf "task_branch_base=%s\n" "$(git rev-parse HEAD)"
    printf "candidate marker %s\n" "$run_id" >>tracked.txt
    printf "candidate file for %s\n" "$run_id" >candidate.txt
    git add tracked.txt candidate.txt >/dev/null 2>&1
    git commit -q -m "candidate change for $run_id" >/dev/null 2>&1
    printf "candidate_commit=%s\n" "$(git rev-parse HEAD)"
    printf "candidate_ref=%s\n" "$(git symbolic-ref --quiet HEAD)"
    git bundle create /home/agent/task.bundle "refs/heads/dca/$run_id" >/dev/null 2>&1
    printf "task_bundle_exit=%s\n" "$?"
    printf "task_bundle_heads=%s\n" "$(git bundle list-heads /home/agent/task.bundle 2>/dev/null | wc -l | tr -d " ")"
    for neg in "$neg_a" "$neg_b"; do
        git checkout -q -B "dca/$neg" "$source_sha" 2>/dev/null
        printf "negative marker %s\n" "$neg" >>tracked.txt
        git commit -q -am "candidate change for $neg" >/dev/null 2>&1
        printf "%s_commit=%s\n" "$neg" "$(git rev-parse HEAD)"
        git bundle create "/home/agent/$neg.bundle" "refs/heads/dca/$neg" >/dev/null 2>&1
        printf "%s_bundle_exit=%s\n" "$neg" "$?"
    done
    printf "probe_complete=yes\n"
' sh "$(git -C "$FIXTURE" rev-parse "refs/heads/$SELECTED")" "$RUN_ID" "$NEG_A" "$NEG_B" \
    >"$WORK/vm.env" 2>"$WORK/vm.err"
record vm_exit "$?"

# Lift the VM-reported identities into the observations for the evaluator.
for key in clone_exit hooks_path task_branch_exit task_branch_base candidate_commit candidate_ref \
    task_bundle_exit task_bundle_heads probe_complete; do
    record "vm_$key" "$(sed -n "s/^$key=//p" "$WORK/vm.env" | head -n 1)"
done
record "vm_${NEG_A}_commit" "$(sed -n "s/^${NEG_A}_commit=//p" "$WORK/vm.env" | head -n 1)"
record "vm_${NEG_B}_commit" "$(sed -n "s/^${NEG_B}_commit=//p" "$WORK/vm.env" | head -n 1)"

# --- host baseline, taken before anything is retrieved ----------------------------------------------

snapshot baseline
record candidate_ref_exists_before "$(git -C "$FIXTURE" show-ref --verify --quiet "refs/heads/dca/$RUN_ID" && echo yes || echo no)"

# --- retrieval: copy out, then validate before any import --------------------------------------------

run_sbx cp "$SANDBOX:/home/agent/task.bundle" "$QUARANTINE/task.bundle" >"$WORK/cp-out.txt" 2>&1
record cp_out_exit "$?"
quarantine_abs=$(cd "$QUARANTINE" && pwd)
case "$quarantine_abs" in
    */.git|*/.git/*) inside_git=yes ;;
    *) inside_git=no ;;
esac
record quarantine_inside_git "$inside_git"
record quarantine_bundle_bytes "$(wc -c <"$QUARANTINE/task.bundle" 2>/dev/null | tr -d ' ')"

git -C "$FIXTURE" bundle verify "$ROOT/$QUARANTINE/task.bundle" >"$WORK/returned-verify.txt" 2>&1
record returned_verify_exit "$?"
git -C "$FIXTURE" bundle list-heads "$ROOT/$QUARANTINE/task.bundle" >"$WORK/returned-heads.txt" 2>&1
record returned_heads_exit "$?"
record returned_head_count "$(grep -c . "$WORK/returned-heads.txt" 2>/dev/null || echo 0)"
record returned_head_line "$(head -n 1 "$WORK/returned-heads.txt" 2>/dev/null)"

# Object-level integrity, in a throwaway repository: `git bundle verify` checks the header and
# prerequisites, and on this git version accepts a truncated bundle, so the bundle is also
# unpacked into a scratch quarantine repo. The host repository is not touched by this.
scratch_fetch() {
    # $1 = bundle path, $2 = ref, $3 = observation prefix
    scratch=$WORK/scratch-$3.git
    rm -rf "$scratch"
    git init -q --bare "$scratch" >/dev/null 2>&1
    git -C "$scratch" fetch --no-tags "$ROOT/$1" "$2:$2" >"$WORK/scratch-$3.txt" 2>&1
    record "${3}_scratch_fetch_exit" "$?"
    record "${3}_scratch_ref" "$(git -C "$scratch" rev-parse --verify --quiet "$2" 2>/dev/null)"
}
scratch_fetch "$QUARANTINE/task.bundle" "refs/heads/dca/$RUN_ID" returned

# Import only when the returned bundle verified and advertises exactly the expected identity.
if python3 gates/G5/record.py --returned-ok "$OBS" "$WORK"; then
    git -C "$FIXTURE" fetch --no-tags --no-write-fetch-head "$ROOT/$QUARANTINE/task.bundle" \
        "refs/heads/dca/$RUN_ID:refs/heads/dca/$RUN_ID" >"$WORK/fetch.txt" 2>&1
    record fetch_exit "$?"
    record fetch_attempted yes
else
    record fetch_attempted no
fi
record candidate_ref_after "$(git -C "$FIXTURE" rev-parse --verify --quiet "refs/heads/dca/$RUN_ID" 2>/dev/null)"

snapshot post

# --- negative A: a corrupted returned bundle ----------------------------------------------------------

run_sbx cp "$SANDBOX:/home/agent/$NEG_A.bundle" "$QUARANTINE/$NEG_A.bundle" >"$WORK/cp-out-$NEG_A.txt" 2>&1
record "cp_out_${NEG_A}_exit" "$?"
record "${NEG_A}_bytes_before" "$(wc -c <"$QUARANTINE/$NEG_A.bundle" 2>/dev/null | tr -d ' ')"
# Truncate the quarantine copy: the VM's bundle is untouched, the returned artefact is not.
dd if="$QUARANTINE/$NEG_A.bundle" of="$QUARANTINE/$NEG_A.truncated" bs=1 \
    count="$(( $(wc -c <"$QUARANTINE/$NEG_A.bundle") / 3 ))" >/dev/null 2>&1
mv "$QUARANTINE/$NEG_A.truncated" "$QUARANTINE/$NEG_A.bundle"
record "${NEG_A}_bytes_after" "$(wc -c <"$QUARANTINE/$NEG_A.bundle" 2>/dev/null | tr -d ' ')"

git -C "$FIXTURE" bundle verify "$ROOT/$QUARANTINE/$NEG_A.bundle" >"$WORK/$NEG_A-verify.txt" 2>&1
record "${NEG_A}_verify_exit" "$?"
scratch_fetch "$QUARANTINE/$NEG_A.bundle" "refs/heads/dca/$NEG_A" "$NEG_A"
if python3 gates/G5/record.py --negative-a-ok "$OBS" "$WORK"; then
    # Only reached if the corrupted bundle verified, which must not happen.
    git -C "$FIXTURE" fetch --no-tags --no-write-fetch-head "$ROOT/$QUARANTINE/$NEG_A.bundle" \
        "refs/heads/dca/$NEG_A:refs/heads/dca/$NEG_A" >"$WORK/$NEG_A-fetch.txt" 2>&1
    record "${NEG_A}_fetch_exit" "$?"
    record "${NEG_A}_fetch_attempted" yes
else
    record "${NEG_A}_fetch_attempted" no
fi
record "${NEG_A}_ref_after" "$(git -C "$FIXTURE" rev-parse --verify --quiet "refs/heads/dca/$NEG_A" 2>/dev/null)"
snapshot after-negative-a

# --- negative B: the retrieval copy itself fails ---------------------------------------------------------
# The VM holds a valid bundle at /home/agent/<neg-b>.bundle; retrieval asks for a path that
# isn't there, so sbx cp fails. No global sbx state is touched.

run_sbx exec "$SANDBOX" sh -c 'test -s "/home/agent/'"$NEG_B"'.bundle" && echo present || echo missing' \
    >"$WORK/$NEG_B-vm-bundle.txt" 2>&1
record "${NEG_B}_vm_bundle" "$(tr -d '\n' <"$WORK/$NEG_B-vm-bundle.txt")"
run_sbx cp "$SANDBOX:/home/agent/does-not-exist-$NEG_B.bundle" "$QUARANTINE/$NEG_B.bundle" \
    >"$WORK/cp-out-$NEG_B.txt" 2>&1
record "cp_out_${NEG_B}_exit" "$?"
record "${NEG_B}_quarantine_exists" "$(test -e "$QUARANTINE/$NEG_B.bundle" && echo yes || echo no)"

if [ -s "$QUARANTINE/$NEG_B.bundle" ] && python3 gates/G5/record.py --negative-b-ok "$OBS" "$WORK"; then
    git -C "$FIXTURE" fetch --no-tags --no-write-fetch-head "$ROOT/$QUARANTINE/$NEG_B.bundle" \
        "refs/heads/dca/$NEG_B:refs/heads/dca/$NEG_B" >"$WORK/$NEG_B-fetch.txt" 2>&1
    record "${NEG_B}_fetch_exit" "$?"
    record "${NEG_B}_fetch_attempted" yes
else
    record "${NEG_B}_fetch_attempted" no
fi
record "${NEG_B}_ref_after" "$(git -C "$FIXTURE" rev-parse --verify --quiet "refs/heads/dca/$NEG_B" 2>/dev/null)"
snapshot after-negative-b

cleanup
trap - EXIT INT TERM

python3 gates/G5/record.py "$OBS" "$WORK"
