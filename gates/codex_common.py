"""Shared helpers for the Codex gates G3 (T019) and G2 (T022).

Standard library only. This module is deliberately THIN: everything that is not specific to the
Codex backend - the preflight, the accepted-G4 fingerprint and its canonicalization, provenance,
the SSH-agent baseline, the sandbox-cleanup rule and the criteria shaping - already exists in
gates/claude_common.py and is re-exported here rather than reimplemented. Only three things
genuinely differ per backend, and only those are written here:

  * which pinned sandbox base the gate's sandboxes must come from (sandbox_bases.codex);
  * which accepted G4 cell defines the network policy (codex/<profile> rather than claude/...);
  * whether a host the inventory has NOT promoted is being treated as promoted.

Importing claude_common from here reads oddly, but the alternative was to move ~250 lines of
already-reviewed, already-committed code, which would reopen the accepted Claude gates for no
behavioral gain. The generic helpers are named for what they do, not for Claude.
"""

import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "gates"))

import claude_common as _shared  # noqa: E402

# --- re-exported, backend-neutral -------------------------------------------------------------------
VERSIONS = _shared.VERSIONS
G4_EVIDENCE = _shared.G4_EVIDENCE
GOVERNANCE_PROBE = _shared.GOVERNANCE_PROBE
rules_module = _shared.rules_module
preflight_module = _shared.preflight_module
g4_module = _shared.g4_module

read_observations = _shared.read_observations
read_json = _shared.read_json
read_file = _shared.read_file
read_versions = _shared.read_versions
fingerprint = _shared.fingerprint
accepted_network_state = _shared.accepted_network_state
preflight = _shared.preflight
preflight_ids = _shared.preflight_ids
fingerprint_unchanged_row = _shared.fingerprint_unchanged_row
cleanup_row = _shared.cleanup_row
criteria_rows = _shared.criteria_rows

BACKEND = "codex"
# The provider and model this block runs under. The model is not hard-coded as a requirement: G3
# reads the provider's actual list and records what it selected, applying T019's documented
# non-deprecated GPT-5.x fallback only if the preferred one is genuinely unavailable.
PROVIDER = "chatgpt"
PREFERRED_MODEL = "gpt-5.6"
# THE LISTING IS NOT THE AVAILABILITY AUTHORITY. `docker agent models --provider chatgpt` lists
# gpt-5.6 and marks it default, but the Codex backend rejects it for a ChatGPT-account sign-in with
# HTTP 400 "The 'gpt-5.6' model is not supported when using Codex with a ChatGPT account". So G3
# enumerates candidates against the RUNTIME and selects the first one the backend actually accepts.
#
# The candidate set is not invented: these are the GPT-5.x model identifiers present as literals in
# the pinned docker_agent_artifact, the same evidence method T014 used for control-plane hosts. They
# are ordered by T019's rule - the preferred model first, then descending GPT-5.x, with a plain
# model before its -codex sibling at the same version - so the first acceptance is the highest
# available non-deprecated GPT-5.x. Enumeration stops at that first acceptance, so the cost is a
# couple of trivial calls, not one per candidate.
MODEL_CANDIDATES = (
    "gpt-5.6",
    "gpt-5.5",
    "gpt-5.4",
    "gpt-5.3-codex",
    "gpt-5.2-codex",
    "gpt-5.1-codex-max",
    "gpt-5.1-codex",
    "gpt-5-codex",
)
# The credential FILE NAME the trusted fallback may copy, and nothing else from the config dir.
CREDENTIAL_FILE = "chatgpt-auth.json"
# Provider API-key variable names, checked for ABSENCE by name only. A value is never read.
API_KEY_NAMES = ("OPENAI_API_KEY", "OPENAI_BASE_URL", "OPENAI_ORG_ID", "ANTHROPIC_API_KEY",
                 "ANTHROPIC_AUTH_TOKEN", "CLAUDE_API_KEY")
# The safety mode every native Codex execution used as V1 evidence must run under. There is no
# weaker fallback: a gate that cannot prove strict makes Codex unavailable.
REQUIRED_SAFETY = "strict"
# The host whose promotion to an in-VM `refresh` purpose needs runtime evidence, never documentation.
REFRESH_CANDIDATE = "auth.openai.com"


def codex_base(versions):
    """(image reference, exact digest) for the pinned Codex sandbox base."""
    base = (versions.get("sandbox_bases") or {}).get(BACKEND) or {}
    return base.get("base"), base.get("version")


def base_identity_row(gate, obs, work, versions, observed_key="sbx_resolved_base"):
    """The gate created its sandboxes from the exact pinned Codex base, read back from sbx.

    Identity is the combination the store actually exposes: the sbx-resolved reference, the cached
    template's repository/tag/id, and the full pinned digest. A tag alone identifies nothing, and
    the short image id is not a digest, so neither is accepted on its own.
    """
    image, digest = codex_base(versions)
    resolved = obs.get(observed_key)
    repository, _, tag = (image or "").rpartition(":")
    cached = preflight_module.template_image(read_json(work, "templates.json"), repository, tag)
    holds = (
        bool(image)
        and bool(digest)
        and resolved == image
        and preflight_module.template_identity_matches(cached, image, digest)
    )
    return (
        f"{gate}.base",
        "every sandbox in this gate came from the exact pinned sandbox_bases.codex base: the "
        "reference sbx resolved, the cached template's repository, tag and image id, and the "
        "pinned digest all agree; no base was discovered or substituted",
        holds,
        f"pinned {image} {digest}; sbx resolved {resolved or 'not reported'}; cached template "
        f"{cached.get('repository') + ':' + cached.get('tag') + ' id ' + str(cached.get('id')) if isinstance(cached, dict) else 'not found in the template store'}",
    )


def codex_policy(profile="trusted"):
    """(allow hosts, explicit deny hosts) for the Codex profile, as accepted G4 defines it.

    The policy is not restated here. It is the same cell G4 derived from the committed T014
    inventory and trusted-allowlist draft and then proved, so a Codex gate cannot run under a
    policy that merely resembles the verified one.
    """
    with open(g4_module.INVENTORY_FILE, encoding="utf-8") as fh:
        inventory = json.load(fh)
    with open(g4_module.DRAFT_FILE, encoding="utf-8") as fh:
        draft = json.load(fh)
    for cell in g4_module.cells(inventory, draft):
        if cell["backend"] == BACKEND and cell["profile"] == profile:
            return list(cell["allow_rules"]), list(cell["deny_rules"])
    raise ValueError(f"accepted G4 defines no {BACKEND}/{profile} cell")


def policy_row(gate, obs, profile="trusted", g4_path=G4_EVIDENCE):
    """The policy this gate applied is exactly the one accepted G4 proved for that profile."""
    problems = []
    try:
        allow, deny = codex_policy(profile)
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as exc:
        return (
            f"{gate}.policy",
            f"the sandboxes ran under the accepted G4 {BACKEND}/{profile} network policy",
            False, f"the accepted policy could not be derived: {exc}",
        )
    try:
        with open(g4_path, encoding="utf-8") as fh:
            proven = (json.load(fh).get("proven_network_allowset") or {}).get(BACKEND, {})
    except (OSError, ValueError):
        proven = {}
    if sorted(allow) != sorted(proven.get(profile) or []):
        problems.append(
            f"the derived allow set does not match accepted G4's proven set for "
            f"{BACKEND}/{profile}")
    applied_allow = (obs.get("policy_allow") or "").split(",") if obs.get("policy_allow") else []
    applied_deny = (obs.get("policy_deny") or "").split(",") if obs.get("policy_deny") else []
    if applied_allow != allow:
        problems.append(f"the applied allow list {applied_allow} is not {allow}")
    if applied_deny != deny:
        problems.append(f"the applied deny list {applied_deny} is not {deny}")
    if obs.get("policy_rules_exit") != "0":
        problems.append(
            f"applying the policy exited {obs.get('policy_rules_exit', 'not reported')!r}")
    return (
        f"{gate}.policy",
        f"every sandbox ran under the accepted G4 {BACKEND}/{profile} network policy: the allow "
        "set is the one G4 proved, and nothing was added for this gate's convenience",
        not problems,
        "; ".join(problems) if problems else
        f"{len(allow)} allow ({', '.join(allow)}); "
        f"{len(deny) if deny else 'no'} explicit deny"
        f"{' (' + ', '.join(deny) + ')' if deny else ''}",
    )


def refresh_candidate_state():
    """How the committed T014 inventory currently classifies the refresh-promotion candidate.

    A gate must never treat `auth.openai.com` as an in-VM host because the OAuth flow uses it on
    the developer's machine. It stays `host-oauth-login` / `sandbox_required=false` until a gate
    produces RUNTIME evidence that the sandboxed process itself must refresh against it, and that
    promotion goes through the documented T014/T015 path with an evidence_ref.
    """
    with open(g4_module.INVENTORY_FILE, encoding="utf-8") as fh:
        inventory = json.load(fh)
    for entry in ((inventory.get("backends") or {}).get(BACKEND) or {}).get("hosts") or []:
        if entry.get("host") == REFRESH_CANDIDATE:
            return {
                "host": REFRESH_CANDIDATE,
                "purpose": entry.get("purpose"),
                "sandbox_required": entry.get("sandbox_required"),
                "profiles": list(entry.get("profiles") or []),
            }
    return None
