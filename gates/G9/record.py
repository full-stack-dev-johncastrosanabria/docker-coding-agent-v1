"""Evaluate G9 and write gates/G9.json (tasks.md T023): control-plane capability non-usability.

Standard library only. The shared preflight, accepted-G4 fingerprint, provenance and cleanup rules
come from gates/claude_common.py; each backend's pinned base and accepted G4 cell come from its own
module. The classification itself is NOT here - it lives in gates/G9/oracle.py, which S5b (T088)
reuses exactly, so this file only gathers observations and hands them to the oracle.

WHAT A PASS MEANS, AND WHAT IT DOES NOT. G9 PASS means a repository-controlled workload, including
one with sudo, could not reproduce the agent's authenticated control-plane capability. It does NOT
by itself make a backend untrusted-eligible: Claude additionally needs G1b PASS and Codex needs G2
PASS, and both of those are accepted FAILs. So whatever G9 returns, both backends remain trusted-only
in V1, untrusted autonomous capability is not claimed, S5a stays mandatory and split-plane stays the
recorded direction. A FAIL here is never resolved by exempting a control-plane host from the policy.

AMBIGUITY IS FAILURE. The gate proves a negative, and a negative indistinguishable from an
unobserved positive has proved nothing. A timeout, a TLS error, a 3xx, a 5xx or a body that cannot
separate an authenticated caller from an unauthenticated one all fail the probe.

Usage:
  python3 gates/G9/record.py --available                 backends whose availability gate is PASS
  python3 gates/G9/record.py --policy <backend> allow|deny
  python3 gates/G9/record.py --preflight <obs> [<work>]
  python3 gates/G9/record.py <obs> [<work>]              write gates/G9.json
"""

import collections
import datetime
import importlib.util
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "gates"))

import claude_common as claude  # noqa: E402
import codex_common as codex  # noqa: E402


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


oracle = _load("g9_oracle", os.path.join(ROOT, "gates", "G9", "oracle.py"))

WORK = os.path.join(ROOT, "gates", "G9", "work")
PROBES = os.path.join(ROOT, "gates", "G9", "probes.json")
EVIDENCE = os.path.join(ROOT, "gates", "G9.json")
VERSIONS = os.path.join(ROOT, "runtime", "versions.yaml")

GATE = "G9"
MARKER = "DCA-G9-OK"

BACKENDS = {
    "claude": {"gate": "G1a", "common": claude, "safety": None, "eligibility_gate": "G1b"},
    "codex": {"gate": "G3", "common": codex, "safety": "strict", "eligibility_gate": "G2"},
}


def read_text(path):
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            return fh.read()
    except OSError:
        return None


def key_values(work, name):
    raw = claude.read_file(work, name)
    if not isinstance(raw, str) or not raw.strip():
        return {}
    out = {}
    for line in raw.splitlines():
        key, sep, value = line.strip().partition("=")
        if sep:
            out[key] = value
    return out


def availability(backend, versions):
    gate = BACKENDS[backend]["gate"]
    path = os.path.join(ROOT, "gates", f"{gate}.json")
    try:
        with open(path, encoding="utf-8") as fh:
            evidence = json.load(fh)
    except (OSError, ValueError):
        return False, f"the {gate} availability evidence could not be read"
    if not isinstance(evidence, dict) or evidence.get("gate") != gate:
        return False, f"the {gate} evidence is not a {gate} document"
    if evidence.get("status") != "PASS":
        return False, f"{gate} is {evidence.get('status')!r}, not PASS, so {backend} is unavailable"
    stale = claude.rules_module.evidence_problems(evidence, versions)
    if stale:
        return False, f"the {gate} evidence is stale: {stale}"
    return True, None


def available_backends(versions):
    return [b for b in ("claude", "codex") if availability(b, versions)[0]]


def eligibility_note(backend):
    """Why this backend stays trusted-only regardless of what G9 returns."""
    gate = BACKENDS[backend]["eligibility_gate"]
    try:
        with open(os.path.join(ROOT, "gates", f"{gate}.json"), encoding="utf-8") as fh:
            status = json.load(fh).get("status")
    except (OSError, ValueError):
        status = None
    return gate, status


def agent_answer(work, name):
    """The model's answer, joined from streamed agent_choice deltas only.

    The echoed prompt contains the marker, so reading it from any text in the document would make a
    run in which the model never answered look successful.
    """
    raw = read_text(os.path.join(work, name))
    if not raw:
        return None
    parts = []
    for line in raw.splitlines():
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if isinstance(event, dict) and event.get("type") == "agent_choice" \
                and isinstance(event.get("content"), str):
            parts.append(event["content"])
    return "".join(parts) if parts else None


def policy_log_hosts(work, name):
    """{host: allowed} from one `sbx policy log --json` capture.

    The pinned sbx v0.43.0 emits two lists, `allowed_hosts` and `blocked_hosts`, each entry keyed by
    a host:port with a `count_since`. This reads that shape exactly rather than guessing at generic
    key names: a parser that cannot find the entries reports no allowed host, which silently fails
    the positive control and makes the whole gate look like a FAIL it is not.

    A host counts as allowed only when it appears under `allowed_hosts` AND traffic was actually
    seen. A rule permitting a host is not evidence that the agent called it.
    """
    document = claude.read_json(work, name)
    if not isinstance(document, dict):
        return {}
    hosts = {}
    for entry in document.get("blocked_hosts") or []:
        if isinstance(entry, dict) and isinstance(entry.get("host"), str):
            hosts.setdefault(entry["host"].split(":")[0], False)
    for entry in document.get("allowed_hosts") or []:
        if not isinstance(entry, dict) or not isinstance(entry.get("host"), str):
            continue
        count = entry.get("count_since")
        seen = True if count is None else isinstance(count, int) and count > 0
        host = entry["host"].split(":")[0]
        hosts[host] = hosts.get(host, False) or seen
    return hosts


def observations_for(work, backend):
    """[safe observation dicts] parsed from both workload captures."""
    out = []
    for privilege in ("user", "sudo"):
        raw = read_text(os.path.join(work, f"workload-{privilege}-{backend}.jsonl"))
        if not raw:
            continue
        for line in raw.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                record_line = json.loads(line)
            except ValueError:
                continue
            if isinstance(record_line, dict):
                out.append(record_line)
    return out


def _ambiguity_kind(observation):
    """What made one result uninformative. Transport outranks status: a request that never
    completed has no status to interpret."""
    if observation.get("transport") != oracle.TRANSPORT_OK:
        return "transport_failure"
    status = observation.get("status")
    if not isinstance(status, int):
        return "transport_failure"
    if status == 429:
        return "rate_limited"
    if 300 <= status < 400:
        return "redirect"
    if status >= 500:
        return "server_error"
    return "indistinguishable_response"


def capability_facts(probes, by_probe, control_ok, status):
    """The typed, machine-readable verdict fields.

    THE DISTINCTION THIS EXISTS TO PRESERVE. G9 proves a NEGATIVE, so two very different outcomes
    both end in FAIL: a workload that actually REPRODUCED the authenticated capability, and a
    provider that would not tell an authenticated caller from an unauthenticated one. Reporting
    both as a bare "FAIL" invites a reader to assume the first, which is the opposite of what the
    second observed. So the two are recorded separately, and they are NOT complements:
    capability_reproduced 'not_observed' together with capability_non_usability 'not_proven' is
    exactly the inconclusive case, and it is still a FAIL because ambiguity fails closed.
    """
    classes = collections.Counter()
    ambiguity = collections.Counter()
    for probe in probes:
        for observation in by_probe.get(probe["id"], []):
            if observation.get("transport") == oracle.TRANSPORT_INAPPLICABLE:
                continue
            result, _, _ = oracle.classify(probe, observation)
            classes[result] += 1
            if result == oracle.AMBIGUOUS:
                ambiguity[_ambiguity_kind(observation)] += 1

    reproduced = "observed" if classes[oracle.MEDIATED] else "not_observed"
    if status == "PASS":
        return {"failure_reason": None, "ambiguity_reason": None,
                "capability_reproduced": reproduced, "capability_non_usability": "proven",
                "classes": dict(classes)}

    if classes[oracle.MEDIATED]:
        failure = "capability_reproduced"
    elif not control_ok:
        failure = "positive_control_failed"
    elif classes[oracle.AMBIGUOUS]:
        failure = "ambiguous_provider_response"
    else:
        failure = "incomplete_probe"
    return {
        "failure_reason": failure,
        "ambiguity_reason": (ambiguity.most_common(1)[0][0]
                             if failure == "ambiguous_provider_response" and ambiguity else None),
        "capability_reproduced": reproduced,
        # Never 'proven' on a FAIL: the gate did not establish the property, whatever the reason.
        "capability_non_usability": "not_proven",
        "classes": dict(classes),
    }


def backend_rows(backend, obs, work, versions, probes, ran):
    module = BACKENDS[backend]["common"]
    safety = BACKENDS[backend]["safety"]
    host = obs.get(f"probe_host_{backend}")
    rows = []

    base = module.base_identity_row(GATE, obs, work, versions,
                                    observed_key=f"sbx_resolved_base_{backend}")
    rows.append((*base, ran))
    policy_obs = dict(obs)
    policy_obs["policy_allow"] = obs.get(f"policy_allow_{backend}")
    policy_obs["policy_deny"] = obs.get(f"policy_deny_{backend}")
    policy_obs["policy_rules_exit"] = obs.get(f"policy_rules_{backend}_exit")
    policy = module.policy_row(GATE, policy_obs, "trusted")
    rows.append((*policy, ran))

    vmstate = key_values(work, f"vmstate-{backend}.txt")
    setup = []
    if vmstate.get("stdin_is_tty") != "no":
        setup.append(f"stdin was a TTY ({vmstate.get('stdin_is_tty')!r})")
    if vmstate.get("agent_binary") != versions.get("docker_agent"):
        setup.append(f"the in-VM docker-agent reported {vmstate.get('agent_binary')!r}")
    if vmstate.get("sudo_resolves_to") != "root":
        setup.append(
            f"sudo resolved to {vmstate.get('sudo_resolves_to')!r}, not root, so the privileged "
            "variant would not answer T023's question")
    if safety:
        # The positive control is Codex evidence, so it is only usable under strict; an agent's
        # `safety` field is merely a session default, so the flag must be on the command line.
        observed_flag = obs.get(f"safety_flag_{backend}")
        if observed_flag != f"--safety {safety}":
            setup.append(
                f"the gate recorded safety flag {observed_flag!r}, not {'--safety ' + safety!r}")
    rows.append((
        f"{GATE}.setup.{backend}",
        "the probing workload really is an unprivileged repository process that can escalate with "
        "sudo to root, running non-interactively against the pinned docker-agent: an unprivileged "
        "failure would not answer whether a PRIVILEGED workload can reproduce the capability"
        + (f", and the positive control ran under --safety {safety}" if safety else ""),
        not setup,
        "; ".join(setup) if setup else
        f"workload user {vmstate.get('workload_user')!r}, sudo resolves to "
        f"{vmstate.get('sudo_resolves_to')!r}, stdin not a TTY, pinned docker-agent "
        f"{versions.get('docker_agent')}"
        + (f", --safety {safety} on the control" if safety else ""),
        ran))
    setup_ok = ran and not setup

    # --- the positive control ---------------------------------------------------------------------
    answer = agent_answer(work, f"control-{backend}.json")
    hosts = policy_log_hosts(work, f"policylog-control-{backend}.json")
    control_problems = []
    if obs.get(f"control_{backend}_exit") != "0":
        control_problems.append(
            f"the legitimate agent path exited {obs.get(f'control_{backend}_exit')!r}")
    elif MARKER not in (answer or ""):
        control_problems.append("the legitimate agent path produced no answer")
    if not hosts.get(host):
        control_problems.append(
            f"the sbx policy log recorded no allowed entry for {host!r}, so the control is not tied "
            "to the probe's endpoint")
    rows.append((
        f"{GATE}.control.{backend}",
        f"positive control: in the SAME sandbox, the legitimate agent path exercised the same "
        f"endpoint and request family on {host} and produced the mediation signal, shown by the "
        "agent's successful authenticated call and the matching sbx policy log entry, captured "
        "BEFORE the workload sent anything. Without this a 401 from the workload would be equally "
        "consistent with the capability simply being unreachable here",
        not control_problems,
        "; ".join(control_problems) if control_problems else
        f"the agent answered {MARKER} and the policy log shows {host} allowed",
        setup_ok))
    control_ok = setup_ok and not control_problems

    # --- the workload variants --------------------------------------------------------------------
    raw_observations = observations_for(work, backend)
    discovery = [o for o in raw_observations if o.get("variant") == "discovery"]
    variants = [o for o in raw_observations if o.get("variant") not in (None, "discovery")]
    by_probe = {}
    for observation in variants:
        by_probe.setdefault(observation.get("probe"), []).append(observation)

    workload_problems = []
    for privilege in ("user", "sudo"):
        if obs.get(f"workload_{privilege}_{backend}_exit") != "0":
            workload_problems.append(
                f"the {privilege} workload exited "
                f"{obs.get(f'workload_{privilege}_{backend}_exit')!r}")
        if not any(d.get("privilege") == privilege for d in discovery):
            workload_problems.append(f"the {privilege} workload recorded no discovery pass")

    positive_controls = {
        p["id"]: {"result_class": oracle.MEDIATED if control_ok else None,
                  "why": "; ".join(control_problems) or "the agent path produced the mediation "
                                                        "signal on this endpoint",
                  "policy_log_host": host if hosts.get(host) else None}
        for p in probes}

    status, per_probe = oracle.evaluate_backend(probes, positive_controls, by_probe)
    capability = capability_facts(probes, by_probe, control_ok, status)
    for probe in probes:
        holds, problems, details = per_probe[probe["id"]]
        # The observed text ALWAYS leads with whether reproduction was actually seen, so a FAIL can
        # never be read as "the workload got in" when what happened was that the provider refused
        # to distinguish callers at all.
        lead = (f"capability_reproduced={capability['capability_reproduced']}, "
                f"capability_non_usability={capability['capability_non_usability']}")
        rows.append((
            f"{GATE}.probe.{probe['id']}",
            f"no repository-controlled workload - without sudo or with it - reproduced the "
            f"authenticated control-plane capability on {probe['host']}: every variant (A no "
            "credential, B every visible non-secret value in every credential position, C the "
            "client's own request structure, D other proxy-triggering representations) received "
            "the provider's documented unauthenticated result, unambiguously",
            holds,
            f"{lead}; " + ("; ".join(problems)[:560] if problems else
                           f"{details.get('variants_run')} variants across "
                           f"{details.get('variant_keys')} all {oracle.UNAUTHENTICATED}; "
                           f"positive control {details.get('positive_control')}"),
            control_ok))
    probes_ok = control_ok and status == "PASS" and not workload_problems

    rows.append((
        f"{GATE}.workload.{backend}",
        "both workload passes completed and screened their discovered values: anything matching "
        "real credential material was DROPPED rather than replayed, because G9 asks whether the "
        "capability is reachable WITHOUT a credential - replaying a real token would only re-prove "
        "G1b and would put a secret in the evidence",
        not workload_problems,
        "; ".join(workload_problems) if workload_problems else
        "; ".join(f"{d.get('privilege')}: {d.get('candidates')} non-secret candidate(s), "
                  f"{d.get('secrets_excluded')} secret(s) excluded" for d in discovery),
        control_ok))

    facts = {
        "host": host,
        "probes": [p["id"] for p in probes],
        "positive_control": "mediated" if control_ok else "failed",
        "variants": len(variants),
        "discovery": [{k: d.get(k) for k in ("privilege", "candidates", "secrets_excluded",
                                             "locations")} for d in discovery],
        "per_probe": {pid: {"holds": h, "problems": pr, "details": de}
                      for pid, (h, pr, de) in per_probe.items()},
        "safety": safety,
        "capability": capability,
    }
    return rows, (probes_ok and not workload_problems), facts


def evaluate(obs, work, versions=None, probes_path=None):
    if versions is None:
        versions = claude.read_versions(VERSIONS)
    if probes_path is None:
        probes_path = PROBES
    all_probes = oracle.load_probes(probes_path)

    shared = [(*row, True) for row in claude.preflight(GATE, obs, work, versions)]
    preflight_ok = all(row[2] for row in shared)

    per_backend, statuses, facts = {}, {}, {}
    for backend in ("claude", "codex"):
        ok, why = availability(backend, versions)
        if not ok:
            per_backend[backend] = []
            statuses[backend] = ("NOT-RUN", why)
            facts[backend] = {"available": False, "not_run_reason": why}
            continue
        ran = preflight_ok and obs.get(f"create_dca-g9-{backend}_exit") == "0"
        probes = oracle.probes_for(all_probes, backend)
        rows, holds, backend_facts = backend_rows(backend, obs, work, versions, probes, ran)
        per_backend[backend] = claude.criteria_rows(rows)
        statuses[backend] = ("PASS" if holds else "FAIL", None)
        backend_facts["available"] = True
        facts[backend] = backend_facts

    tail = list(shared)
    fingerprint_row, before, _ = claude.fingerprint_unchanged_row(GATE, obs, work)
    tail.append((*fingerprint_row, "policy_after_exit" in obs))
    names = tuple(f"dca-g9-{b}" for b in ("claude", "codex"))
    tail.append((*claude.cleanup_row(GATE, obs, work, names), "ls_after_exit" in obs))

    return claude.criteria_rows(tail), per_backend, statuses, before, facts


def record(obs_path, work=WORK, versions_path=VERSIONS, evidence_path=EVIDENCE, probes_path=None):
    obs = claude.read_observations(obs_path)
    versions = claude.read_versions(versions_path)
    criteria, per_backend, statuses, fingerprint, facts = evaluate(
        obs, work, versions, probes_path)

    backends = {}
    for backend, (status, why) in statuses.items():
        entry = {"status": status}
        if why is not None:
            entry["not_run_reason"] = why
        capability = (facts.get(backend) or {}).get("capability")
        if capability:
            # Typed so T024 and T088 never parse prose to tell an observed capability reproduction
            # apart from a provider that would not disambiguate. Both are FAIL; they mean opposite
            # things.
            entry["failure_reason"] = capability["failure_reason"]
            entry["ambiguity_reason"] = capability["ambiguity_reason"]
            entry["capability_reproduced"] = capability["capability_reproduced"]
            entry["capability_non_usability"] = capability["capability_non_usability"]
        if per_backend[backend]:
            entry["criteria"] = per_backend[backend]
        backends[backend] = entry

    live = [b for b, e in backends.items() if e["status"] != "NOT-RUN"]
    overall = ("NOT-RUN" if not live
               else "PASS" if all(backends[b]["status"] == "PASS" for b in live)
               else "FAIL")

    implications = {}
    for backend in live:
        gate, status = eligibility_note(backend)
        entry = backends[backend]
        # Say in words exactly what the typed fields say, so no reader of this document can take a
        # FAIL to mean the workload got in when that is not what was observed.
        if entry["status"] == "PASS":
            verdict = (f"G9 PASS for {backend}: no workload variant reproduced the capability at "
                       "either privilege level, and non-usability is PROVEN.")
        elif entry.get("failure_reason") == "ambiguous_provider_response":
            verdict = (
                f"G9 FAIL for {backend}, and the reason is INCONCLUSIVE EVIDENCE, not a breach: "
                f"failure_reason=ambiguous_provider_response, ambiguity_reason="
                f"{entry.get('ambiguity_reason')}. capability_reproduced=not_observed - NO workload "
                "request produced a completion or any authenticated mediation signal - but "
                "capability_non_usability=not_proven, because the provider's answers could not "
                "separate an authenticated caller from an unauthenticated one. Ambiguity fails "
                "CLOSED by design, so this is correctly a FAIL; it must NOT be read as the workload "
                "reproducing authenticated capability.")
        else:
            verdict = (f"G9 FAIL for {backend}: failure_reason={entry.get('failure_reason')}, "
                       f"capability_reproduced={entry.get('capability_reproduced')}, "
                       f"capability_non_usability={entry.get('capability_non_usability')}.")
        implications[backend] = (
            f"{verdict} Untrusted eligibility additionally requires {gate} PASS, and {gate} is "
            f"{status}, so {backend} remains TRUSTED-ONLY in V1: untrusted autonomous capability is "
            "not claimed, S5a stays mandatory, and split-plane agent/workload separation stays the "
            "recorded future direction.")

    evidence = {
        "gate": GATE,
        "status": overall,
        "run_at": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "versions": {
            "sbx": (claude.read_json(work, "pf-version.json") or {}).get("client", {}).get("version")
            or None
        },
        "provenance": claude.rules_module.evidence_provenance(GATE, versions),
        "criteria": criteria,
        "backends": backends,
        "notes": (
            "Control-plane capability NON-USABILITY per backend (tasks.md T023, research R13). G1b "
            "and G2 asked whether a repository workload can READ credential material; G9 asks "
            "whether it can USE the agent's authenticated control-plane capability anyway. Each "
            "available backend is probed on the one runtime control-plane host accepted G4 proved "
            "for it - api.anthropic.com for Claude, chatgpt.com for Codex - with the cheapest "
            "harmless request in the authenticated family the backend's own pinned client uses. "
            "The interpretation is never a bare HTTP status: each probe carries a STRUCTURAL "
            "discriminator from the provider's own JSON taxonomy, and a result matching neither "
            "the unauthenticated nor the mediation pattern is AMBIGUOUS, which fails. Every probe "
            "also carries a positive control in the SAME sandbox, where the legitimate agent path "
            "exercises the SAME endpoint and request family and produces the mediation signal, "
            "captured with its sbx policy log entry BEFORE the workload sends anything; without it "
            "a 401 from the workload would be equally consistent with the capability being "
            "unreachable here, and the gate would be vacuous. Each workload ran twice, once "
            "unprivileged and once as root via sudo, across variants A (no credential material), B "
            "(every visible NON-SECRET sentinel, placeholder, helper or proxy value replayed in "
            "every credential position the client uses), C (the client's own non-secret request "
            "structure, bare and with each B value) and D (other proxy-triggering representations "
            "actually exposed in the VM). Discovered values matching real credential material are "
            "DROPPED and only counted, never replayed and never recorded: G9 asks whether the "
            "capability is reachable WITHOUT a credential, and replaying a real token would only "
            "re-prove G1b while putting a secret in the evidence. Recorded facts are probe id, "
            "variant id, privilege, credential position, discovery location id, opaque value "
            "index, HTTP status, content type, body byte length and matched pattern ids - never a "
            "token, an Authorization value, a cookie, a credential file's contents or a response "
            "body. The oracle in gates/G9/oracle.py and the probe set in gates/G9/probes.json are "
            "reused EXACTLY by S5b (T088). G9 alone never makes a backend untrusted-eligible: that "
            "additionally requires G1b PASS for Claude and G2 PASS for Codex, both of which are "
            "accepted FAILs, so both backends remain trusted-only in V1 whatever this gate "
            "returns. A FAIL here is never resolved by exempting a control-plane host. Every rule "
            "this gate added carried --sandbox; the global fingerprint is captured with zero "
            "sandboxes before and after; sbx policy init, sbx reset and sbx rm --all are never "
            "called; sandboxes were created and removed one at a time by name."
        ),
    }
    if implications:
        evidence["notes"] += " IMPLICATIONS: " + " ".join(implications.values())
    if fingerprint is not None:
        evidence["network_policy_fingerprint"] = fingerprint
    with open(evidence_path, "w", encoding="utf-8") as fh:
        fh.write(json.dumps(evidence, indent=2, ensure_ascii=False) + "\n")
    return evidence, facts


def main(argv):
    args = argv[1:]
    mode = args.pop(0) if args and args[0].startswith("--") else None

    if mode == "--available":
        print(" ".join(available_backends(claude.read_versions(VERSIONS))))
        return 0

    if mode == "--policy":
        if len(args) != 2 or args[0] not in BACKENDS:
            print("usage: record.py --policy claude|codex allow|deny", file=sys.stderr)
            return 2
        backend, which = args
        module = BACKENDS[backend]["common"]
        allow, deny = (module.claude_policy("trusted") if backend == "claude"
                       else module.codex_policy("trusted"))
        emit = {"allow": allow, "deny": deny}.get(which)
        if emit is None:
            print("usage: record.py --policy claude|codex allow|deny", file=sys.stderr)
            return 2
        print(",".join(emit))
        return 0

    path = args[0] if args else None
    work = args[1] if len(args) > 1 else WORK
    if path is None or mode not in (None, "--preflight"):
        print("usage: python3 gates/G9/record.py "
              "[--available|--policy|--preflight] <observations.env> [<work-dir>]", file=sys.stderr)
        return 2

    if mode == "--preflight":
        obs = claude.read_observations(path)
        rows = claude.preflight(GATE, obs, work, claude.read_versions(VERSIONS))
        for identifier, description, result, observed in rows:
            print(f"  {identifier} {'PASS' if result else 'FAIL'} {description}")
            print(f"          observed: {observed}")
        return 0 if all(row[2] for row in rows) else 1

    evidence, _ = record(path, work)
    print(f"G9: {evidence['status']} ({EVIDENCE})")
    for row in evidence["criteria"]:
        print(f"  {row['id']:<22} {row['result']:<8} {row['description'][:66]}")
    for backend, entry in evidence["backends"].items():
        print(f"  --- {backend}: {entry['status']}"
              + (f" ({entry.get('not_run_reason')})" if entry.get("not_run_reason") else ""))
        for row in entry.get("criteria", []):
            print(f"      {row['id']:<34} {row['result']:<8} {row['description'][:58]}")
            print(f"              observed: {row['evidence_ref'][:300]}")
    return 0 if evidence["status"] in ("PASS", "NOT-RUN") else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
