"""Native deny rules derived from `actions.yaml` (tasks.md T053, T055, T060).

Standard library only. Both backends' native permission rules are generated HERE, from the same
DENY classes, so "the two deny lists derive from the same policy" is a fact the parity test can
recompute rather than a claim someone maintained by hand in two files.

NATIVE RULES ARE DEFENCE IN DEPTH, NOT THE POLICY. The policy is `actions.yaml`, evaluated by
`dca-gate` for every tool call on both backends. A native rule can only reject a call **before** the
gate sees it, which is acceptable exactly because it is at least as strict. That is also why there
are no native `allow` or `ask` rules anywhere: a custom allow rule would execute a call without the
gate ever running, which would silently remove the control the whole design rests on.

NOT EVERY DENY CLASS IS EXPRESSIBLE NATIVELY, and pretending otherwise would be worse than not
trying. `GATE_ONLY` names each class the native layer cannot state and why, so the gap is recorded
instead of being mistaken for coverage:

  * **5** (any path outside the workspace and scratch dir) needs a realpath computed per call -
    symlink escapes included - which no static glob can express;
  * **16** (install onto the host) has no in-VM representation at all: the VM cannot reach the
    host, so there is nothing to deny;
  * **26** (non-allowlisted skill or subagent) needs the trusted-source check - name plus kit
    manifest hash - which a pattern cannot perform. Only its crudest case, `run_skill`, is
    expressible;
  * **27** (any mutating tool used by the researcher or reviewer) is agent-scoped, and these rules
    are config-wide;
  * **29** (any tool call after the step limit) depends on run state that does not exist at
    configuration time.
"""

import json
import os

_HERE = os.path.dirname(os.path.abspath(__file__))
ACTIONS_PATH = os.path.join(os.path.dirname(os.path.dirname(_HERE)),
                            "runtime", "policy", "actions.yaml")

#: DENY classes no static permission rule can express, and the reason for each.
GATE_ONLY = {
    5: "needs a per-call realpath, including symlink escapes, that no static glob can express",
    16: "the VM cannot reach the host, so there is nothing to deny natively",
    26: "needs the trusted-source check (kit-manifest hash), not just a tool name",
    27: "is agent-scoped, and native permission rules are config-wide",
    29: "depends on run state that does not exist at configuration time",
}

#: Paths that ARE the agent's own policy, limits, instructions and gate state (class 25).
#:
#: `actions.yaml` states class 25 as `/run/dca/**`, but the gate decides the run scratch dir
#: `/run/dca/out/` first (class 6, ALLOW): it is where the root MUST write `context.json`, `plan.md`
#: and `report.agent.json`. A static glob cannot say "except /run/dca/out", so the native rules name
#: every other thing the launcher puts under /run/dca instead. Anything else there is still class 5
#: or 25 at the gate; a native `/run/dca/**` rejected the required scratch writes before the gate saw
#: them (dca bench, Codex K1: `write_file` of /run/dca/out/context.json denied, run blocked).
SELF_PATHS = (
    "/opt/dca/**", "/etc/claude-code/**",
    "/run/dca/run.json", "/run/dca/grants.json", "/run/dca/task.txt",
    "/run/dca/grants/**", "/run/dca/state/**", "/run/dca/cagent/**",
)

#: Shell command shapes for the DENY classes that name an action rather than a path. Deliberately
#: narrow: each pattern denies something that is unambiguously the denied class, because a
#: too-broad native rule would reject legitimate work before the gate could classify it properly.
SHELL_DENY = {
    19: [  # force-push / history rewrite of a shared or remote branch
        "git push --force*", "git push -f *", "git push --force-with-lease*",
        "git push --mirror*", "git push --delete*", "git push origin :*",
    ],
    22: [  # deployment or production-system action
        "kubectl *", "helm *", "terraform apply*", "terraform destroy*",
        "aws deploy*", "aws ecs update-service*", "gcloud app deploy*", "gcloud run deploy*",
        "az webapp deploy*", "fly deploy*", "serverless deploy*", "docker push *",
    ],
    23: [  # credential create/modify/rotate/revoke; security-policy change
        "ssh-keygen *", "gpg --gen-key*", "gpg --full-generate-key*",
        "aws iam *", "aws sts assume-role*", "gcloud auth *", "gcloud iam *",
        "az ad *", "vault write *", "op item *", "security add-generic-password*",
        "chmod 777 *", "sudo passwd*", "useradd *", "usermod *",
    ],
}

#: Tools that browse or search the open web (class 30), per backend.
BROWSE_DENY = {
    "claude": ["WebSearch", "WebFetch"],
    "codex": ["fetch*", "open_url*", "web_search*", "browser*", "api*"],
}

#: Claude tools that read, and that write, a path argument.
CLAUDE_READ_TOOLS = ("Read", "NotebookRead")
CLAUDE_WRITE_TOOLS = ("Write", "Edit", "NotebookEdit")
#: Docker Agent filesystem tools, by what they do with a `path` argument. These are the tool names
#: the pinned runtime actually exposes (verified with `docker agent debug toolsets --json` against
#: the staged config), not names taken from documentation: a pattern naming a tool that does not
#: exist reads like coverage while denying nothing. `read_multiple_files` is absent because its
#: argument is a LIST, which `tool:arg=pattern` cannot match - class 2 for it is the gate's job,
#: and the gate resolves every path argument regardless of tool.
CODEX_READ_TOOLS = ("read_file",)
CODEX_WRITE_TOOLS = ("write_file", "edit_file", "create_directory", "remove_directory")


def load_actions(path=None):
    with open(path or ACTIONS_PATH, encoding="utf-8") as handle:
        return json.load(handle)


def deny_class_ids(actions):
    """Every class that is DENY at BOTH trust levels, in ascending order.

    A class that is DENY at only one trust level is not a native rule candidate: a config-wide rule
    cannot know the run's trust level, and `actions.yaml` defines no such class today anyway.
    """
    return sorted(
        entry["id"] for entry in actions["classes"]
        if entry["decision"]["trusted"] == "DENY" and entry["decision"]["untrusted"] == "DENY")


def _sensitive_globs(actions):
    return list(actions["policy"]["sensitive_globs"])


def claude_deny_rules(actions=None):
    """Claude managed-settings `permissions.deny` rules, sorted and de-duplicated."""
    actions = actions or load_actions()
    rules = set()
    for glob in _sensitive_globs(actions):                       # class 2
        for tool in CLAUDE_READ_TOOLS + CLAUDE_WRITE_TOOLS:
            rules.add(f"{tool}({glob})")
    for glob in SELF_PATHS:                                      # class 25
        for tool in CLAUDE_WRITE_TOOLS:
            rules.add(f"{tool}({glob})")
    for patterns in SHELL_DENY.values():                         # classes 19, 22, 23
        for pattern in patterns:
            rules.add(f"Bash({pattern})")
    for tool in BROWSE_DENY["claude"]:                           # class 30
        rules.add(tool)
    return sorted(rules)


def codex_deny_rules(actions=None):
    """Docker Agent top-level `permissions.deny` patterns, sorted and de-duplicated.

    Pattern syntax is Docker Agent's: a tool name with optional globbing, and optional argument
    matching as `tool:arg=pattern`.
    """
    actions = actions or load_actions()
    rules = set()
    for glob in _sensitive_globs(actions):                       # class 2
        for tool in CODEX_READ_TOOLS + CODEX_WRITE_TOOLS:
            rules.add(f"{tool}:path={glob}")
    for glob in SELF_PATHS:                                      # class 25
        for tool in CODEX_WRITE_TOOLS:
            rules.add(f"{tool}:path={glob}")
    for patterns in SHELL_DENY.values():                         # classes 19, 22, 23
        for pattern in patterns:
            rules.add(f"shell:cmd={pattern}")
    for tool in BROWSE_DENY["codex"]:                            # class 30
        rules.add(tool)
    rules.add("run_skill*")                                      # class 26, its crudest case only
    return sorted(rules)


def covered_classes(actions=None):
    """The DENY classes the native layer really states, and the ones only the gate can."""
    actions = actions or load_actions()
    ids = deny_class_ids(actions)
    return ([i for i in ids if i not in GATE_ONLY],
            [i for i in ids if i in GATE_ONLY])
