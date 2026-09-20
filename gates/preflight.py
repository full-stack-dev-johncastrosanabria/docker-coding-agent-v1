"""Shared read-only global-state checks for the gates after G0 (tasks.md T009 onwards).

Standard library only. G0 establishes the global sbx state; every later gate re-checks it,
read-only, before it creates a sandbox, and stops without repairing anything on drift. Keeping
the checks here means one definition of "the post-G0 state" instead of one per gate.

Shapes are what the sbx version pinned by G0 emits, observed on this host, not a promised
schema; an sbx upgrade re-runs the gates (research R27) and may need these adapted.
"""

MISSING = object()

SSH_FORWARDING_KEY = "ssh.agentForwardingEnabled"
SSH_SOCKET_KEY = "ssh.agentSocketPath"

# The G0 bootstrap network rule, exactly as sbx reports it. It must be the only network rule:
# any further allow or deny, whatever its scope, origin or layer, is drift from the post-G0
# baseline. Filesystem rules are a different resource type and are ignored.
BOOTSTRAP_RULE = {
    "id": "default-deny-all",
    "scope": "global",
    "applies_to": "all",
    "resource_type": "network",
    "decision": "deny",
    "resources": ["**"],
    "origin": "local",
    "layer": "local",
    "status": "active",
}


def bootstrap_rule_matches(rule):
    """True only for the exact G0 bootstrap rule, field by field."""
    return isinstance(rule, dict) and all(rule.get(key) == value for key, value in BOOTSTRAP_RULE.items())


def network_rules(policy):
    """The network rules of an `sbx policy ls --json` document."""
    rules = (policy or {}).get("rules", []) if isinstance(policy, dict) else []
    return [
        rule
        for rule in rules
        if isinstance(rule, dict) and str(rule.get("resource_type", "")).startswith("network")
    ]


def network_baseline_holds(policy):
    """True only when the network rule set is exactly the G0 bootstrap rule."""
    rules = network_rules(policy)
    return len(rules) == 1 and bootstrap_rule_matches(rules[0])


def setting_value(document, key):
    """The evaluated value from `sbx settings get --json`, or MISSING when not reported."""
    if isinstance(document, dict) and document.get("key") == key and "value" in document:
        return document["value"]
    return MISSING


def version_pins_hold(version, exact):
    """True when the sbx client and server both report the pinned exact version and run."""
    if not isinstance(version, dict):
        return False
    client = (version.get("client") or {}).get("version")
    server = version.get("server") or {}
    return (
        client == exact
        and server.get("version") == exact
        and server.get("state") == "running"
    )


def ssh_forwarding_refusals(forwarding, socket_path):
    """Why a run must be refused on SSH-agent grounds, as reasons (empty = nothing to refuse).

    This is the read-only detector T010 requires, and the rule the launcher applies in its
    preconditions. V1's baseline is deliberately stricter than "forwarding is off": it also
    requires no fixed host agent socket to be configured.

    Whether a fixed `ssh.agentSocketPath` alone would forward an agent while
    `ssh.agentForwardingEnabled` is false has **not** been proven on the pinned sbx version, so
    this doesn't claim it bypasses the flag. It is refused because a configured host agent
    socket is a forwarding intent that V1 does not accept unverified. A value that cannot be
    read is also a refusal, never read as "no agent".
    """
    reasons = []
    enabled = setting_value(forwarding, SSH_FORWARDING_KEY)
    if enabled is MISSING or not isinstance(enabled, bool):
        reasons.append(f"{SSH_FORWARDING_KEY} could not be read")
    elif enabled:
        reasons.append(f"{SSH_FORWARDING_KEY} is true, so an agent would be forwarded")

    fixed = setting_value(socket_path, SSH_SOCKET_KEY)
    if fixed is MISSING or not isinstance(fixed, str):
        reasons.append(f"{SSH_SOCKET_KEY} could not be read")
    elif fixed.strip():
        reasons.append(
            f"{SSH_SOCKET_KEY} names a fixed host agent socket; V1 refuses that rather than "
            "assume it is inert while forwarding is disabled, which the pinned version has not "
            "been shown to guarantee"
        )
    return reasons


# --- in-sandbox SSH-agent isolation ------------------------------------------------------------

def ssh_agent_isolation_failures(probe):
    """Why a sandbox might reach an SSH agent, as reasons (empty = the invariant holds).

    The invariant is: **no usable forwarded SSH-agent endpoint exists and no host SSH agent is
    reachable.** A dangling SSH_AUTH_SOCK is acceptable only with all of the corroborating
    evidence, because the sandbox runtime sets that variable to the fixed in-VM path where its
    relay socket would appear when forwarding is enabled:

    - SSH_AGENT_PID is unset;
    - SSH_AUTH_SOCK is unset, or its path neither exists nor is a Unix socket;
    - ssh-add cannot connect to an agent. "The agent has no identities" does **not** count: that
      means an agent answered with zero keys loaded;
    - no candidate forwarded agent socket was found.

    Missing or unparsable evidence fails closed.
    """
    reasons = []

    if probe.get("ssh_agent_pid") != "unset":
        reasons.append(f"SSH_AGENT_PID is {probe.get('ssh_agent_pid', 'not reported')!r}")

    sock = probe.get("ssh_auth_sock")
    if sock == "unset":
        pass
    elif sock == "set":
        if probe.get("ssh_auth_sock_exists") != "no":
            reasons.append(
                f"SSH_AUTH_SOCK points at {probe.get('ssh_auth_sock_path', 'an unreported path')}, "
                f"which exists ({probe.get('ssh_auth_sock_exists', 'not reported')})"
            )
        if probe.get("ssh_auth_sock_is_socket") != "no":
            reasons.append(
                f"SSH_AUTH_SOCK points at {probe.get('ssh_auth_sock_path', 'an unreported path')}, "
                f"which is a socket ({probe.get('ssh_auth_sock_is_socket', 'not reported')})"
            )
    else:
        reasons.append(f"SSH_AUTH_SOCK state is {sock!r}, which was not observed")

    # An agent that answers is a reachable agent, whether or not it holds keys.
    if probe.get("ssh_add_present") != "yes":
        reasons.append(f"ssh-add availability is {probe.get('ssh_add_present', 'not reported')!r}")
    elif probe.get("ssh_add_cannot_connect") != "yes":
        reasons.append(
            f"ssh-add did not report that it cannot connect (exit "
            f"{probe.get('ssh_add_exit', 'not reported')}, cannot-connect "
            f"{probe.get('ssh_add_cannot_connect', 'not reported')!r}); an agent that answers, "
            "even with no identities loaded, is reachable"
        )

    sockets = probe.get("agent_sockets")
    if sockets != "0":
        reasons.append(f"candidate agent sockets found: {sockets!r} {probe.get('socket_paths', '')}".strip())
    return reasons


# --- sandbox base identity ----------------------------------------------------------------------

def template_image(templates, repository, tag):
    """The cached template with this exact repository and tag from `sbx template ls --json`.

    A tag alone doesn't identify a template: the repository must match too (the store reports it
    fully qualified, for example docker.io/docker/sandbox-templates).
    """
    if not isinstance(templates, dict) or not isinstance(templates.get("images"), list):
        return None
    for image in templates["images"]:
        if (
            isinstance(image, dict)
            and image.get("tag") == tag
            and image.get("repository") in (repository, f"docker.io/{repository}")
        ):
            return image
    return None


def template_identity_matches(cached, image, digest):
    """True when the cached template is this exact repository and tag and its image id prefixes
    the full digest.

    The short image id is not a digest on its own. The identity is the combination: the
    sbx-resolved reference, the cached repository/tag/id, and the full digest.
    """
    repository, _, tag = (image or "").rpartition(":")
    identifier = cached.get("id") if isinstance(cached, dict) else None
    return (
        bool(repository)
        and bool(tag)
        and isinstance(cached, dict)
        and cached.get("tag") == tag
        and cached.get("repository") in (repository, f"docker.io/{repository}")
        and isinstance(identifier, str)
        and len(identifier) >= 12
        and isinstance(digest, str)
        and digest.startswith(f"sha256:{identifier}")
    )
