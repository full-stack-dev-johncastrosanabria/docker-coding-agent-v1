"""Evaluate G11 part A and write gates/G11.json (tasks.md T020): event-stream integrity.

Standard library only. The shared preflight, accepted-G4 fingerprint, provenance and cleanup rules
come from gates/claude_common.py; the Codex base and policy come from gates/codex_common.py. Each
backend is judged with ITS OWN pinned base and ITS OWN accepted G4 cell, never a shared assumption.

THIS GATE STAYS PARTIAL. Part A proves criteria 1-4. Criteria 5-8 - host step limits, retry cycles,
wall-clock stop semantics and FR-023a success-at-limit - are T073's, and nothing here may report
them. `status` is therefore PARTIAL even when every part-A criterion passes, and the per-backend
`backends.<name>.status` carries the part-A verdict.

THE GROUND TRUTH IS NOT THE STREAM. Criterion 1 compares the parser's count against markers the
tool calls left in the VM filesystem, recorded before any parsing. Checking a stream against itself
would prove only that the parser is self-consistent.

A backend whose availability gate is not a current PASS is recorded NOT-RUN with a reason, and one
backend failing part A never fails the other.
"""

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


pp = _load("g11_probe_parser", os.path.join(ROOT, "gates", "G11", "probe_parser.py"))

WORK = os.path.join(ROOT, "gates", "G11", "work")
CAPTURES = os.path.join(ROOT, "gates", "G11", "captures")
EVIDENCE = os.path.join(ROOT, "gates", "G11.json")
VERSIONS = os.path.join(ROOT, "runtime", "versions.yaml")

GATE = "G11"
EXPECTED_N = 3
SPOOF_CALL_ID = "DCA-G11-SPOOF-CALL"
SPOOF_SESSION = "DCA-G11-SPOOF-SESSION"

# Each backend, its availability gate, and the module that knows its pinned base and G4 cell.
BACKENDS = {
    "claude": {"gate": "G1a", "common": claude, "safety": None},
    "codex": {"gate": "G3", "common": codex, "safety": "strict"},
}

PART_B = [
    "5 host step-limit enforcement",
    "6 retry-cycle enforcement",
    "7 wall-clock / host limit stop semantics",
    "8 FR-023a success-at-limit behavior",
]


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
    """(is available, why not) from the backend's accepted availability evidence."""
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


def derive(directory):
    """Write the host-simulated criterion-3 variants beside a real sanitized capture.

    No model runs for this: mutating stream bytes is a host operation, and spending a live run on it
    would buy nothing. Both mutations are deterministic so the evidence is reproducible.
    """
    source = os.path.join(directory, "count.jsonl")
    text = read_text(source)
    if text is None:
        return 1, f"{source} could not be read"
    lines = [l for l in text.split("\n") if l.strip()]
    if len(lines) < 4:
        return 1, f"{source} has too few lines to mutate"

    # MALFORMED: drop the closing brace of a NON-final line, so the object boundary is broken where
    # the stream is still mid-flight. Removing an arbitrary interior byte is not enough - it usually
    # lands inside a string value and leaves valid JSON with a mangled field.
    malformed = list(lines)
    index = min(2, len(lines) - 2)
    malformed[index] = malformed[index].rstrip()[:-1]
    with open(os.path.join(directory, "malformed.jsonl"), "w", encoding="utf-8") as fh:
        fh.write("\n".join(malformed) + "\n")

    # TRUNCATED: cut part-way through the final line, which also removes the terminal event - exactly
    # what a stream that stopped mid-write looks like.
    last = lines[-1]
    truncated = "\n".join(lines[:-1]) + "\n" + last[:max(1, len(last) // 2)]
    with open(os.path.join(directory, "truncated.jsonl"), "w", encoding="utf-8") as fh:
        fh.write(truncated)
    return 0, "derived malformed.jsonl and truncated.jsonl"


def _criterion(identifier, description, holds, observed, applicable=True):
    return (identifier, description, holds, observed, applicable)


def spoof_outer_events(text):
    """How many OUTER events carry the imitation's own identity. It must be zero.

    The imitation travels inside a string value, so its newlines are escaped and it cannot become a
    line of its own. This counts the thing that would be true if that ever stopped holding: an outer
    event whose id or session is the planted one.
    """
    found = 0
    for line in text.split("\n"):
        if not line.strip():
            continue
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if not isinstance(event, dict):
            continue
        call = event.get("tool_call")
        if (isinstance(call, dict) and call.get("id") == SPOOF_CALL_ID) \
                or event.get("tool_call_id") == SPOOF_CALL_ID \
                or event.get("session_id") == SPOOF_SESSION:
            found += 1
    return found


def backend_rows(backend, obs, work, versions, ran):
    """Criteria 1-4 for one backend, plus its base and policy bindings."""
    module = BACKENDS[backend]["common"]
    safety = BACKENDS[backend]["safety"]
    captures = os.path.join(CAPTURES, backend)
    rows = []

    base = module.base_identity_row(GATE, obs, work, versions,
                                    observed_key=f"sbx_resolved_base_{backend}")
    rows.append((*base, ran))
    # policy_row reads the generic observation names, so this backend's names are restated for it.
    policy_obs = dict(obs)
    policy_obs["policy_allow"] = obs.get(f"policy_allow_{backend}")
    policy_obs["policy_deny"] = obs.get(f"policy_deny_{backend}")
    policy_obs["policy_rules_exit"] = obs.get(f"policy_rules_{backend}_exit")
    policy = module.policy_row(GATE, policy_obs, "trusted")
    rows.append((*policy, ran))

    # --- how the runs were actually invoked ---------------------------------------------------------
    vmstate = key_values(work, f"vmstate-{backend}.txt")
    invocation = []
    if vmstate.get("stdin_is_tty") != "no":
        invocation.append(f"stdin was a TTY ({vmstate.get('stdin_is_tty')!r})")
    if vmstate.get("agent_binary") != versions.get("docker_agent"):
        invocation.append(
            f"the in-VM docker-agent reported {vmstate.get('agent_binary')!r}, not the pinned "
            f"{versions.get('docker_agent')!r}")
    if safety:
        # Codex evidence is only usable under strict, and an agent's `safety` field is merely a
        # session default, so the flag must be on the command line of every invocation.
        observed_flag = obs.get(f"safety_flag_{backend}")
        if observed_flag != f"--safety {safety}":
            invocation.append(
                f"the gate recorded safety flag {observed_flag!r}, not {'--safety ' + safety!r}")
    rows.append(_criterion(
        f"{GATE}.invocation.{backend}",
        "every run on this backend was non-interactive against the pinned docker-agent"
        + (f", with --safety {safety} on the command line rather than relying on a config default"
           if safety else ""),
        not invocation,
        "; ".join(invocation) if invocation else
        f"stdin not a TTY, pinned docker-agent {versions.get('docker_agent')}"
        + (f", --safety {safety} on every invocation" if safety else ""),
        ran))

    bound = ran and base[2] and policy[2] and not invocation

    # --- criterion 1: a known N real tool calls -> exactly N countable events ------------------------
    count = pp.parse(read_text(os.path.join(captures, "count.jsonl")) or "")
    markers = key_values(work, f"markers-{backend}.txt").get("markers")
    problems = []
    if obs.get(f"count_{backend}_exit") != "0":
        problems.append(f"the run exited {obs.get(f'count_{backend}_exit')!r}")
    if not count.ok:
        problems.append(f"the capture is {count.classification} ({count.problems[:1]})")
    if markers != str(EXPECTED_N):
        problems.append(
            f"the VM holds {markers!r} tool-call markers, not {EXPECTED_N} - the ground truth "
            "itself is not what the task asked for, so the parser has nothing to be right about")
    if count.tool_calls != EXPECTED_N:
        problems.append(f"the parser counted {count.tool_calls}, not {EXPECTED_N}")
    if len(set(count.tool_call_ids)) != count.tool_calls:
        problems.append("the same tool_call id was counted more than once")
    rows.append(_criterion(
        f"{GATE}.c1.{backend}",
        f"criterion 1: {EXPECTED_N} genuine tool calls produce exactly {EXPECTED_N} countable outer "
        f"{pp.TOOL_CALL_EVENT} events - not N-1, not N+1, and no id counted twice. The ground truth "
        "is the markers the calls left in the VM filesystem, read before anything was parsed",
        not problems,
        "; ".join(problems) if problems else
        f"{EXPECTED_N} markers in the VM, parser counted {count.tool_calls} "
        f"{pp.TOOL_CALL_EVENT} events over {len(set(count.tool_call_ids))} distinct ids, "
        f"stream {count.classification}",
        bound))
    c1_ok = bound and not problems

    # --- criterion 2: imitation JSON stays payload ---------------------------------------------------
    spoof_text = read_text(os.path.join(captures, "spoof.jsonl")) or ""
    spoof = pp.parse(spoof_text)
    spoof_problems = []
    if obs.get(f"spoof_{backend}_exit") != "0":
        spoof_problems.append(f"the run exited {obs.get(f'spoof_{backend}_exit')!r}")
    if not spoof.ok:
        spoof_problems.append(f"the capture is {spoof.classification}")
    # The test is vacuous unless the imitation actually reached the stream.
    if SPOOF_CALL_ID not in spoof_text:
        spoof_problems.append(
            "the imitation never reached the capture, so nothing was resisted and a clean result "
            "here would mean nothing")
    if SPOOF_CALL_ID in spoof.tool_call_ids:
        spoof_problems.append("the imitated tool call was COUNTED as a real one")
    injected = spoof_outer_events(spoof_text)
    if injected:
        spoof_problems.append(
            f"{injected} OUTER event(s) carry the imitation's identity, so printed JSON was "
            "promoted to an event")
    genuine = spoof.tool_calls
    if genuine != 1:
        spoof_problems.append(f"expected the 1 genuine tool call, the parser counted {genuine}")
    rows.append(_criterion(
        f"{GATE}.c2.{backend}",
        "criterion 2: a genuine tool call printed lines that imitate real Docker Agent events. The "
        "imitation is present in the capture as tool OUTPUT, the genuine call is counted once, and "
        "the imitation adds exactly zero outer events - tool output is payload and can never "
        "become an event",
        not spoof_problems,
        "; ".join(spoof_problems) if spoof_problems else
        f"the imitation is present in the capture and added {injected} outer events; the genuine "
        f"call was counted once ({genuine})",
        c1_ok))
    c2_ok = c1_ok and not spoof_problems

    # --- criterion 3: malformed and truncated fail closed --------------------------------------------
    malformed = pp.parse(read_text(os.path.join(captures, "malformed.jsonl")) or "")
    truncated = pp.parse(read_text(os.path.join(captures, "truncated.jsonl")) or "")
    fail_problems = []
    if malformed.classification != pp.MALFORMED:
        fail_problems.append(f"the malformed variant classified {malformed.classification!r}")
    if truncated.classification != pp.TRUNCATED:
        fail_problems.append(f"the truncated variant classified {truncated.classification!r}")
    for name, result in (("malformed", malformed), ("truncated", truncated)):
        if result.ok:
            fail_problems.append(f"the {name} variant was accepted as a valid successful stream")
        outcome, _ = pp.run_outcome(result, 0)
        if outcome == pp.SUCCESS:
            fail_problems.append(f"the {name} variant produced a SUCCESS outcome on a clean exit")
    rows.append(_criterion(
        f"{GATE}.c3.{backend}",
        "criterion 3: host-simulated malformed and truncated streams, derived deterministically "
        "from this backend's real capture, are classified fail-closed. Nothing is repaired, and a "
        "valid prefix followed by a damaged or missing tail is never accepted as success",
        not fail_problems,
        "; ".join(fail_problems) if fail_problems else
        f"malformed -> {malformed.classification}, truncated -> {truncated.classification}; "
        "neither is a successful stream",
        c2_ok))
    c3_ok = c2_ok and not fail_problems

    # --- criterion 4: abrupt termination -------------------------------------------------------------
    killed = pp.parse(read_text(os.path.join(captures, "kill.jsonl")) or "")
    kill_exit = obs.get(f"kill_{backend}_agent_exit")
    outcome, why = pp.run_outcome(killed, kill_exit)
    kill_problems = []
    if obs.get(f"kill_{backend}_stream_started") in (None, "", "0"):
        kill_problems.append(
            "the run never reached stream_started, so nothing in flight was actually killed")
    if obs.get(f"kill_{backend}_signal_exit") != "0":
        kill_problems.append(
            f"the kill signal was not delivered (pkill exited "
            f"{obs.get(f'kill_{backend}_signal_exit')!r})")
    if outcome != pp.ABNORMAL:
        kill_problems.append(f"the run outcome was {outcome!r}, not {pp.ABNORMAL!r}")
    if outcome == pp.SUCCESS:
        kill_problems.append("an abruptly killed agent was reported as a SUCCESSFUL run")
    rows.append(_criterion(
        f"{GATE}.c4.{backend}",
        "criterion 4: Docker Agent was killed abruptly mid-run from the host-side procedure. The "
        "run is classified abnormal and can never read as success - the process outcome is "
        "decisive and a stream that happens to look finished cannot overrule it. This is abnormal "
        "TERMINATION, not the host limit enforcement T073 adds",
        not kill_problems,
        "; ".join(kill_problems) if kill_problems else
        f"agent_exit={kill_exit} -> {outcome}: {why}; the partial stream classified "
        f"{killed.classification}",
        c3_ok))
    part_a = c3_ok and not kill_problems

    facts = {
        "expected_tool_calls": EXPECTED_N,
        "observed_tool_calls": count.tool_calls,
        "ground_truth_markers": markers,
        "countable_event_type": pp.TOOL_CALL_EVENT,
        "safety": safety,
        "stream_classifications": {
            "count": count.classification, "spoof": spoof.classification,
            "malformed": malformed.classification, "truncated": truncated.classification,
            "kill": killed.classification,
        },
        "kill_outcome": outcome,
        "kill_exit": kill_exit,
        "event_types_seen": sorted(set(count.event_types) | set(spoof.event_types)
                                   | set(killed.event_types)),
        "unknown_event_types": sorted(set(count.unknown_types) | set(spoof.unknown_types)
                                      | set(killed.unknown_types)),
    }
    return rows, part_a, facts


def evaluate(obs, work, versions=None):
    if versions is None:
        versions = claude.read_versions(VERSIONS)
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
        ran = preflight_ok and obs.get(f"create_dca-g11-{backend}_exit") == "0"
        rows, part_a, backend_facts = backend_rows(backend, obs, work, versions, ran)
        per_backend[backend] = claude.criteria_rows(rows)
        statuses[backend] = ("PASS" if part_a else "FAIL", None)
        backend_facts["available"] = True
        facts[backend] = backend_facts

    tail = list(shared)
    fingerprint_row, before, _ = claude.fingerprint_unchanged_row(GATE, obs, work)
    tail.append((*fingerprint_row, "policy_after_exit" in obs))
    names = tuple(f"dca-g11-{b}" for b in ("claude", "codex"))
    tail.append((*claude.cleanup_row(GATE, obs, work, names), "ls_after_exit" in obs))

    return claude.criteria_rows(tail), per_backend, statuses, before, facts


def record(obs_path, work=WORK, versions_path=VERSIONS, evidence_path=EVIDENCE):
    obs = claude.read_observations(obs_path)
    versions = claude.read_versions(versions_path)
    criteria, per_backend, statuses, fingerprint, facts = evaluate(obs, work, versions)

    backends = {}
    for backend, (status, why) in statuses.items():
        entry = {"status": status}
        if why is not None:
            entry["not_run_reason"] = why
        if per_backend[backend]:
            entry["criteria"] = per_backend[backend]
        backends[backend] = entry

    # PART A NEVER REPORTS MORE THAN PART A. Even with every criterion green the gate is PARTIAL
    # until T073 proves criteria 5-8, so this status is not derived from the results at all.
    evidence = {
        "gate": GATE,
        "status": "PARTIAL",
        "run_at": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "versions": {
            "sbx": (claude.read_json(work, "pf-version.json") or {}).get("client", {}).get("version")
            or None
        },
        "provenance": claude.rules_module.evidence_provenance(GATE, versions),
        "criteria": criteria,
        "backends": backends,
        "notes": (
            "G11 PART A only (tasks.md T020): event-stream integrity for every available backend. "
            "The gate status is PARTIAL by construction and stays PARTIAL until T073 proves "
            f"criteria {', '.join(PART_B)}; nothing here reports on them, and no host-side limit "
            "is implemented or claimed. The prototype parser is gates/G11/probe_parser.py, which "
            "T040 will build src/dca/events.py from; it is gate-local and deliberately supports "
            "only the pinned docker_agent " + str(versions.get("docker_agent")) + ". "
            "A tool call is counted from the typed outer event "
            f"{pp.TOOL_CALL_EVENT!r} and never from text that looks like one. That matters because "
            "partial_tool_call, tool_call_response, hook_blocked and tool_call_confirmation all "
            "carry a tool_call object with the SAME key set as a real tool_call event, so only the "
            "type string separates them: partial_tool_call repeats the id of the call it is "
            "streaming, and G3's approval cases recorded 0 tool_call with 1 tool_call_response "
            "each because a hook denied the call - counting either would count attempts and "
            "deltas as dispatched calls. The recognized set is the "
            f"{len(pp.RECOGNIZED_EVENTS)} outer types the pinned runtime was actually OBSERVED "
            "emitting across this repository's real captures; it is not guessed from "
            "documentation and not read out of the binary, whose Go string table is grouped by "
            "length and interleaves unrelated constants. An unrecognized type fails closed rather "
            "than being tolerated. Criterion 1's ground truth is the markers the tool calls left "
            "in the VM filesystem, read before any parsing, so the stream is never checked against "
            "itself. Criterion 3 is derived deterministically on the host from each backend's real "
            "capture, because mutating stream bytes needs no model run. Captures under "
            "gates/G11/captures/<backend>/ are sanitized by redacting credential-shaped STRING "
            "VALUES only, leaving every type, key and nesting level where the runtime put it; a "
            "textual redaction would rewrite object boundaries and make the capture parse as "
            "malformed, which would make the gate a test of its own sanitizer. Each backend used "
            "its own exact pinned base and its own accepted G4 cell; Codex additionally ran "
            "--safety strict on every invocation and carried only the minimal chatgpt-auth.json "
            "the accepted G2 decision selected, never the full ~/.config/cagent. One backend "
            "failing part A never fails the other. Every rule this gate added carried --sandbox; "
            "the global fingerprint is captured with zero sandboxes before and after; sbx policy "
            "init, sbx reset and sbx rm --all are never called; sandboxes were created and removed "
            "one at a time by name."
        ),
    }
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

    if mode == "--derive":
        if len(args) != 1:
            print("usage: record.py --derive <captures-dir>", file=sys.stderr)
            return 2
        status, message = derive(args[0])
        print(message, file=sys.stderr if status else sys.stdout)
        return status

    path = args[0] if args else None
    work = args[1] if len(args) > 1 else WORK
    if path is None or mode not in (None, "--preflight"):
        print("usage: python3 gates/G11/record.py "
              "[--available|--policy|--derive|--preflight] <observations.env> [<work-dir>]",
              file=sys.stderr)
        return 2

    if mode == "--preflight":
        obs = claude.read_observations(path)
        rows = claude.preflight(GATE, obs, work, claude.read_versions(VERSIONS))
        for identifier, description, result, observed in rows:
            print(f"  {identifier} {'PASS' if result else 'FAIL'} {description}")
            print(f"          observed: {observed}")
        return 0 if all(row[2] for row in rows) else 1

    evidence, _ = record(path, work)
    print(f"G11: {evidence['status']} ({EVIDENCE})  [part A only; criteria 5-8 pending T073]")
    for row in evidence["criteria"]:
        print(f"  {row['id']:<22} {row['result']:<8} {row['description'][:70]}")
    for backend, entry in evidence["backends"].items():
        print(f"  --- {backend}: part A {entry['status']}"
              + (f" ({entry.get('not_run_reason')})" if entry.get("not_run_reason") else ""))
        for row in entry.get("criteria", []):
            print(f"      {row['id']:<22} {row['result']:<8} {row['description'][:66]}")
            print(f"              observed: {row['evidence_ref']}")
    return 0 if all(e["status"] in ("PASS", "NOT-RUN") for e in evidence["backends"].values()) else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
