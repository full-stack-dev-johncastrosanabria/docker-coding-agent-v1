#!/bin/sh
# Gate G1d: managed Claude subagents, skills and memory (tasks.md T018).
#
# POSIX sh. Every sbx call goes through run_sbx, which removes SSH_AUTH_SOCK first (research R14).
#
# The question is whether a hostile repository can SHADOW the trusted managed assets. The fixture
# repo ships a same-named .claude/agents/dca-reviewer.md that also claims a wider toolset, a
# same-named .claude/skills/verification/SKILL.md, a NESTED variant under pkg/sub, and a skill that
# is not on the managed allowlist. Each hostile copy carries a distinct marker.
#
# MANAGED PLACEMENT. Claude Code reads managed subagents from `.claude/agents/` and managed skills
# from `.claude/skills/<name>/SKILL.md` INSIDE the managed settings directory, which on Linux is
# /etc/claude-code - so /etc/claude-code/.claude/agents and /etc/claude-code/.claude/skills. The
# managed CLAUDE.md and managed-settings.json sit at the top of that directory instead. An earlier
# revision of this gate installed the agents and skills one level too high, at /etc/claude-code/agents
# and /etc/claude-code/skills, which are not discovery roots: the trusted copies were then never
# candidates, the managed-only dca-researcher never appeared, and the hostile project and nested
# verification copies loaded unopposed. That is a placement defect, not a precedence result.
#
# PRECEDENCE IS RECORDED, NOT INFERRED, and it is recorded from a FIRST-PARTY source. Claude Code's
# Skill tool result carries no skill bytes - it is the fixed string `Launching skill: <name>` - so
# the delivered body is read from Claude Code's own session transcript, from the single metadata
# record linked to that exact Skill invocation by `sourceToolUseID`. gates/G1d/extract.py does that
# INSIDE the VM and emits only identity fields, digests, marker names and PASS/FAIL facts, so no
# unrelated transcript content becomes gate evidence. The model's own verbatim quote, which the
# Docker Agent event stream carries, is kept as corroboration only, never as the authority.
#
# FOUR RUNS in one sandbox:
#   precedence-root, precedence-nested  the authoritative T018 proof, managed settings as V1 pins
#                                       them. The hostile copies ARE candidates and must lose.
#   lockout-root, lockout-nested        the same fixture with strictPluginOnlyCustomization for
#                                       skills and agents added to the managed settings, which
#                                       removes repository skills and agents from the candidate set
#                                       altogether. Defense in depth; it never substitutes for the
#                                       precedence proof.
#
# Both working contexts are exercised in each phase: the repository root and the nested pkg/sub
# directory, which is where the nested hostile copy would win if project-local discovery beat the
# managed scope.
#
# One mountless sandbox, --skills off, from the exact pinned sandbox_bases.claude base, under the
# network policy accepted G4 proved for claude/trusted. Created and removed by name.

set -u

ROOT=$(CDPATH='' cd -- "$(dirname -- "$0")/../.." && pwd) || exit 1
cd "$ROOT" || exit 1

WORK=gates/G1d/work
OBS=$WORK/observations.env
KIT=gates/G6/work/kit
AGENT=gates/G1d/agent.yaml
SANDBOX=dca-g1d
AGENT_BIN=/opt/dca/bin/docker-agent
GOVERNANCE_PROBE=example.invalid:443

MANAGED_DIR=/etc/claude-code
MANAGED_ASSETS=$MANAGED_DIR/.claude
TRUSTED_SKILL=$MANAGED_ASSETS/skills/verification/SKILL.md
REPO_IN_VM=/tmp/dca-g1d-hostile-repo
NESTED_IN_VM=$REPO_IN_VM/pkg/sub

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
    python3 gates/G1d/record.py "$OBS" "$WORK"
    exit 1
}

case $WORK in
    gates/G1d/work) rm -rf "$WORK" || exit 1 ;;
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
    SSH_AUTH_SOCK=/nonexistent/g1d-env-probe.sock
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

python3 gates/G1d/record.py --preflight "$OBS" "$WORK" || stop

POLICY_ALLOW=$(python3 gates/G1d/record.py --policy allow) || stop
POLICY_DENY=$(python3 gates/G1d/record.py --policy deny) || stop
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

# --- stage the managed assets, the hostile repository and the in-VM extractor -----------------------
cp_status=0
run_sbx cp gates/G1d/managed "$SANDBOX":/tmp/g1d-managed \
    >"$WORK/cp-managed-assets.txt" 2>&1 || cp_status=1
run_sbx cp gates/G1d/managed-settings.json "$SANDBOX":/tmp/g1d-managed-settings.json \
    >"$WORK/cp-managed.txt" 2>&1 || cp_status=1
run_sbx cp gates/G1d/managed-settings-lock.json "$SANDBOX":/tmp/g1d-managed-settings-lock.json \
    >"$WORK/cp-managed-lock.txt" 2>&1 || cp_status=1
run_sbx cp gates/G1d/hostile-repo "$SANDBOX":"$REPO_IN_VM" >"$WORK/cp-repo.txt" 2>&1 || cp_status=1
run_sbx cp "$AGENT" "$SANDBOX":/tmp/agent.yaml >"$WORK/cp-agent.txt" 2>&1 || cp_status=1
run_sbx cp gates/G1d/extract.py "$SANDBOX":/tmp/g1d-extract.py \
    >"$WORK/cp-extract.txt" 2>&1 || cp_status=1
run_sbx cp gates/G1d/task-precedence.txt "$SANDBOX":/tmp/g1d-task-precedence.txt \
    >"$WORK/cp-task-precedence.txt" 2>&1 || cp_status=1
run_sbx cp gates/G1d/task-lockout.txt "$SANDBOX":/tmp/g1d-task-lockout.txt \
    >"$WORK/cp-task-lockout.txt" 2>&1 || cp_status=1
record cp_status "$cp_status"

# Managed assets are root-owned under the managed settings directory, in the `.claude` subtree Claude
# Code discovers them from. The workload can read them and can modify none of them, which is what
# makes "managed" mean more than "shipped first".
run_sbx exec "$SANDBOX" sh -c "
    set -e
    sudo install -d -m 0755 -o root -g root $MANAGED_DIR
    sudo install -d -m 0755 -o root -g root $MANAGED_ASSETS
    sudo cp -R /tmp/g1d-managed/agents $MANAGED_ASSETS/agents
    sudo cp -R /tmp/g1d-managed/skills $MANAGED_ASSETS/skills
    sudo install -m 0644 -o root -g root /tmp/g1d-managed/CLAUDE.md $MANAGED_DIR/CLAUDE.md
    sudo install -m 0644 -o root -g root /tmp/g1d-managed-settings.json $MANAGED_DIR/managed-settings.json
    sudo chown -R root:root $MANAGED_DIR
    sudo chmod -R a+rX $MANAGED_DIR
    sudo rm -rf /tmp/g1d-managed /tmp/g1d-managed-settings.json
    find $MANAGED_DIR -type f | sort
" >"$WORK/install.txt" 2>&1
record install_exit "$?"

run_sbx exec "$SANDBOX" sh -c "
    printf 'trusted_verification_sha256=%s\n' \"\$(sha256sum $TRUSTED_SKILL | cut -d' ' -f1)\"
    printf 'managed_dir_owner=%s\n' \"\$(stat -c '%U:%G:%a' $MANAGED_DIR)\"
    printf 'managed_assets_owner=%s\n' \"\$(stat -c '%U:%G:%a' $MANAGED_ASSETS)\"
    printf 'agent_can_write_managed=%s\n' \"\$(test -w $TRUSTED_SKILL && echo yes || echo no)\"
    printf 'agent_can_write_managed_agent=%s\n' \"\$(test -w $MANAGED_ASSETS/agents/dca-reviewer.md && echo yes || echo no)\"
    printf 'managed_skill_present=%s\n' \"\$(test -e $TRUSTED_SKILL && echo present || echo absent)\"
    printf 'managed_researcher_present=%s\n' \"\$(test -e $MANAGED_ASSETS/agents/dca-researcher.md && echo present || echo absent)\"
    printf 'managed_reviewer_present=%s\n' \"\$(test -e $MANAGED_ASSETS/agents/dca-reviewer.md && echo present || echo absent)\"
    printf 'managed_memory_present=%s\n' \"\$(test -e $MANAGED_DIR/CLAUDE.md && echo present || echo absent)\"
    printf 'hostile_project_skill=%s\n' \"\$(test -e $REPO_IN_VM/.claude/skills/verification/SKILL.md && echo present || echo absent)\"
    printf 'hostile_nested_skill=%s\n' \"\$(test -e $NESTED_IN_VM/.claude/skills/verification/SKILL.md && echo present || echo absent)\"
    printf 'hostile_agent=%s\n' \"\$(test -e $REPO_IN_VM/.claude/agents/dca-reviewer.md && echo present || echo absent)\"
    printf 'not_allowlisted_skill=%s\n' \"\$(test -e $REPO_IN_VM/.claude/skills/not-allowlisted/SKILL.md && echo present || echo absent)\"
    printf 'claude_code_version=%s\n' \"\$(claude --version 2>/dev/null | head -1)\"
" >"$WORK/layout.txt" 2>"$WORK/layout.err"
record layout_exit "$?"

# The trusted bytes, read back from the VM, so the comparison is against what was really installed
# rather than against the host copy.
run_sbx exec "$SANDBOX" sh -c "cat $TRUSTED_SKILL" \
    >"$WORK/trusted-verification.md" 2>"$WORK/trusted-verification.err"
record trusted_skill_exit "$?"

# --- the adversarial runs ---------------------------------------------------------------------------
# precedence: the hostile copies are candidates and must lose on precedence alone.
# lockout:    strictPluginOnlyCustomization removes them from the candidate set entirely.
for phase in precedence lockout; do
    if [ "$phase" = lockout ]; then
        run_sbx exec "$SANDBOX" sh -c "
            set -e
            sudo install -m 0644 -o root -g root /tmp/g1d-managed-settings-lock.json \
                $MANAGED_DIR/managed-settings.json
            sudo rm -f /tmp/g1d-managed-settings-lock.json
            sha256sum $MANAGED_DIR/managed-settings.json
            cat $MANAGED_DIR/managed-settings.json
        " >"$WORK/lock-settings.txt" 2>&1
        record lock_settings_exit "$?"
        probe=not-allowlisted
    else
        probe=""
    fi

    run_sbx exec "$SANDBOX" sh -c "
        printf 'phase=%s\n' $phase
        printf 'settings_sha256=%s\n' \"\$(sha256sum $MANAGED_DIR/managed-settings.json | cut -d' ' -f1)\"
        printf 'spawn_depth_env=%s\n' \"\${CLAUDE_CODE_MAX_SUBAGENT_SPAWN_DEPTH:-<unset>}\"
        printf 'managed_settings_depth=%s\n' \"\$(python3 -c \"import json;print(json.load(open('$MANAGED_DIR/managed-settings.json')).get('env',{}).get('CLAUDE_CODE_MAX_SUBAGENT_SPAWN_DEPTH','<unset>'))\")\"
        printf 'strict_plugin_only=%s\n' \"\$(python3 -c \"import json;print(json.load(open('$MANAGED_DIR/managed-settings.json')).get('strictPluginOnlyCustomization','<unset>'))\")\"
    " >"$WORK/settings-$phase.txt" 2>"$WORK/settings-$phase.err"
    record "settings_${phase}_exit" "$?"

    for context in root nested; do
        case $context in
            root)   cwd=$REPO_IN_VM ;;
            nested) cwd=$NESTED_IN_VM ;;
        esac
        label=$phase-$context

        # Which Claude Code transcripts exist BEFORE this run, so the one this run writes is
        # identified by set difference rather than by mtime or by name.
        run_sbx exec "$SANDBOX" sh -c \
            'ls -1 "$HOME"/.claude/projects/*/*.jsonl 2>/dev/null | sort >/tmp/g1d-before.txt; wc -l </tmp/g1d-before.txt' \
            >"$WORK/before-$label.txt" 2>&1
        record "before_${phase}_${context}_exit" "$?"

        # The task text is read from a file INSIDE the VM. Interpolating it into the command would
        # let its own quoting split it into several positional arguments, which Docker Agent would
        # deliver as several separate user messages.
        run_sbx exec "$SANDBOX" sh -c \
            "cd $cwd && exec $AGENT_BIN run --exec --json /tmp/agent.yaml \"\$(cat /tmp/g1d-task-$phase.txt)\" </dev/null" \
            >"$WORK/task-$label.json" 2>"$WORK/task-$label.err"
        record "task_${phase}_${context}_exit" "$?"

        # First-party proof, extracted and sanitized in the VM: only identity fields, digests,
        # marker names and PASS/FAIL facts come back.
        run_sbx exec "$SANDBOX" sh -c \
            "python3 /tmp/g1d-extract.py $label /tmp/g1d-before.txt $TRUSTED_SKILL verification $probe" \
            >"$WORK/transcript-$label.json" 2>"$WORK/transcript-$label.err"
        record "transcript_${phase}_${context}_exit" "$?"
    done
done

run_sbx rm --force "$SANDBOX" >"$WORK/rm-$SANDBOX.txt" 2>&1
record "rm_${SANDBOX}_exit" "$?"
rm -f "$WORK/created-$SANDBOX"

cleanup
trap - EXIT INT TERM

python3 gates/G1d/record.py "$OBS" "$WORK"
