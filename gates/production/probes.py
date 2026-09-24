"""The T062 gate-payload probes, as data (tasks.md T062 items 4, 6, 7, 8).

Each probe is one hook payload in the backend's OWN native shape, fed to the PRODUCTION
`/opt/dca/bin/dca-gate` inside the sandbox. The gate is the single decision point both backends
share, so its answers are checked against the production wrapper, the production interpreter and
the production trusted root rather than against an imported copy on the host.

`expect` is the exit status the contract requires: 0 allow, 2 deny. `deny_class` is the class the
`DCA_DENY <class>:` line on stderr must name, so a probe cannot pass by being denied for the wrong
reason - a denial with the wrong class is as much a conformance failure as no denial at all.
"""

#: Where the fixture's hostile `shellparse.py` writes if it is ever imported. The gate must never
#: create it: `-I` drops the script directory and the cwd, and `env -i` drops PYTHONPATH.
HOSTILE_MODULE_MARKER = "/tmp/dca-t062-hostile-shellparse"

#: A hostile environment of exactly the variables a repository `env` block could set. The gate
#: wrapper's `env -i` plus `python3 -I` must make all of it inert.
HOSTILE_ENV = (
    "PYTHONPATH=/workspace "
    "PYTHONHOME=/tmp/dca-t062-nonexistent "
    "PYTHONSTARTUP=/workspace/shellparse.py "
    "BASH_ENV=/workspace/bashenv.sh "
    "ENV=/workspace/bashenv.sh "
    "LD_PRELOAD=/workspace/evil.so "
)

#: The credential path the gate must refuse to read. It is the real production location the
#: launcher stages the Codex token file into, so this is the path that actually matters.
CREDENTIAL_PATH = "/run/dca/cagent/chatgpt-auth.json"


def _claude(tool, tool_input, agent_type=None):
    payload = {"hook_event_name": "PreToolUse", "tool_name": tool, "tool_input": tool_input,
               "cwd": "/workspace"}
    if agent_type:
        payload["agent_type"] = agent_type
    return payload


def _codex(tool, tool_input, agent="root"):
    return {"hook_event_name": "pre_tool_use", "agent_name": agent, "tool_name": tool,
            "tool_input": tool_input, "cwd": "/workspace"}


#: Probes with this prefix run before a Context Record exists; all others run after one is staged.
ORDERING_PREFIX = "ordering."

#: A valid Context Record, staged the way a run's agent writes it (FR-001).
CONTEXT_RECORD = {
    "classification": {"value": "direct", "reason": "T062 gate probe"},
    "repository_map": {"scope": "minimal", "target_files": ["answer.txt"]},
    "verification_approach": {"type": "deterministic", "checks": [
        {"id": "sh check.sh", "command_or_method": "sh check.sh", "required": True}]},
    "plan_ref": None,
}


def payloads(backend):
    """{name: (payload, expected_exit, deny_class_or_None, env_prefix)} for one backend."""
    make = _claude if backend == "claude" else _codex
    read = "Read" if backend == "claude" else "read_file"
    write = "Write" if backend == "claude" else "write_file"
    shell = "Bash" if backend == "claude" else "shell"
    skill = "Skill" if backend == "claude" else "read_skill"
    delegate = "Task" if backend == "claude" else "transfer_task"
    target = "subagent_type" if backend == "claude" else "agent"
    researcher = "dca-researcher" if backend == "claude" else "researcher"
    reviewer = "dca-reviewer" if backend == "claude" else "reviewer"

    def sub(tool, tool_input, role):
        return (_claude(tool, tool_input, agent_type=role) if backend == "claude"
                else _codex(tool, tool_input, agent={"dca-researcher": "researcher",
                                                     "dca-reviewer": "reviewer"}.get(role, role)))

    probes = {
        # --- the positive control. Without it every denial below would be vacuous. -------------
        "allow.workspace_read": (make(read, {"path": "/workspace/README.md"}), 0, None, ""),
        "allow.workspace_write": (make(write, {"path": "/workspace/answer.txt",
                                               "content": "ready\n"}), 0, None, ""),
        "allow.declared_check": (make(shell, {"command": "sh check.sh"}), 0, None, ""),
        "allow.skill_verification": (make(skill, {"name": "verification"}), 0, None, ""),
        "allow.delegate_researcher": (make(delegate, {target: researcher, "prompt": "probe"}),
                                      0, None, ""),
        "allow.delegate_reviewer": (make(delegate, {target: reviewer, "prompt": "probe"}),
                                    0, None, ""),

        # --- FR-001 (T081): the gate refuses verification and file changes until a Context Record
        # exists, and only those. These run BEFORE the harness stages a record; every other probe
        # runs after it, as it would in a real run (see ORDERING_PREFIX). ---------------------------
        "ordering.read_before_record": (make(read, {"path": "/workspace/README.md"}), 0, None, ""),
        "ordering.inspect_before_record": (make(shell, {"command": "ls /workspace"}), 0, None, ""),
        "ordering.verify_before_record": (make(shell, {"command": "sh check.sh"}), 2, 8, ""),
        "ordering.write_before_record": (make(write, {"path": "/workspace/answer.txt",
                                                      "content": "ready\n"}), 2, 3, ""),

        # --- item 6B / item 4 marker: the sensitive-path denial ----------------------------------
        "deny.credential_read": (make(read, {"path": CREDENTIAL_PATH}), 2, 2, ""),
        "deny.kit_read": (make(read, {"path": "/opt/dca/policy/actions.yaml"}), 2, 2, ""),
        "deny.outside_workspace": (make(read, {"path": "/etc/shadow"}), 2, 5, ""),

        # --- item 6A / item 8: class 26 and its positive complement ------------------------------
        "deny.run_skill": (make("run_skill", {"name": "verification"}), 2, 26, ""),
        "deny.skill_not_allowlisted": (make(skill, {"name": "not-allowlisted"}), 2, 26, ""),
        "deny.skill_hostile_name": (make(skill, {"name": "../../workspace/.claude/skills/"
                                                         "verification"}), 2, 26, ""),
        "deny.delegate_other": (make(delegate, {target: "hostile-helper", "prompt": "probe"}),
                                2, 26, ""),
        "deny.researcher_delegates": (sub(delegate, {target: reviewer, "prompt": "probe"},
                                          researcher), 2, 26, ""),
        "deny.researcher_skill": (sub(skill, {"name": "verification"}, researcher), 2, 26, ""),
        "deny.reviewer_skill": (sub(skill, {"name": "verification"}, reviewer), 2, 26, ""),

        # --- item 7: class 27, the read-only roles ------------------------------------------------
        "deny.reviewer_write": (sub(write, {"path": "/workspace/reviewer-probe.txt",
                                            "content": "x"}, reviewer), 2, 27, ""),
        "deny.researcher_write": (sub(write, {"path": "/workspace/researcher-probe.txt",
                                              "content": "x"}, researcher), 2, 27, ""),
        "deny.reviewer_shell": (sub(shell, {"command": "rm -rf /workspace/answer.txt"}, reviewer),
                                2, 27, ""),

        # --- item 4: the environment a hostile repository controls is inert ------------------------
        "hostile_env.allow_still_allows": (make(read, {"path": "/workspace/README.md"}),
                                           0, None, HOSTILE_ENV),
        "hostile_env.deny_still_denies": (make(read, {"path": CREDENTIAL_PATH}), 2, 2, HOSTILE_ENV),
    }
    if backend == "claude":
        # Claude's own delegation shape: an unknown agent_type is refused before anything else.
        probes["deny.unknown_agent_type"] = (
            _claude(read, {"path": "/workspace/README.md"}, agent_type="hostile-agent"),
            2, None, "")
    else:
        # A Codex payload that omits agent_name must not fall through to the Claude path.
        probes["deny.missing_agent_name"] = (
            {"hook_event_name": "pre_tool_use", "tool_name": read,
             "tool_input": {"path": "/workspace/README.md"}}, 2, None, "")
    return probes
