"""Evaluate G2 and write gates/G2.json (tasks.md T022): ChatGPT OAuth isolation and the V1
credential-mechanism selection.

Standard library only. The shared preflight, accepted-G4 fingerprint, pinned-base verification,
provenance and cleanup rules come from gates/codex_common.py.

THE DECISION THIS GATE MAKES. If the native provider works in two consecutive completely fresh
sandboxes that hold NO credential file, and a privileged scan finds no OpenAI OAuth token material,
then host-side proxy-managed OAuth is the preferred mechanism for BOTH profiles
(`credential_mechanism: proxy-managed`). Otherwise G3's already-proven trusted token-file fallback
stands (`credential_mechanism: token-file-trusted-only`): trusted Codex continues, untrusted stays
blocked, there is no `harness: codex`, and no API key is introduced.

A FAIL IS A LEGITIMATE OUTCOME. It does NOT make Codex unavailable, and it is never avoided by
weakening the test. Two things keep the test honest:

  * the credential file must exist NOWHERE in the VM, proven by searching the filesystem for it,
    not by trusting that the gate skipped a copy. With the fallback present a successful run would
    say nothing about the proxy;
  * the scanner must first detect a clearly synthetic canary, which is then removed before the real
    scan. Without that control a scanner that matches nothing is indistinguishable from a perfectly
    isolated VM - the exact false negative G1b's first pass produced.

`credential_mechanism` alone never makes Codex untrusted-eligible. G9 decides capability
non-usability later; this gate only selects how the credential is delivered.

No token value, partial value, digest, Authorization header, cookie or credential file content is
ever read or recorded. The scan emits pattern ids, location ids, found flags, counts and paths.

Usage:
  python3 gates/G2/record.py --policy allow|deny        emit the accepted G4 codex/trusted rules
  python3 gates/G2/record.py --model                    the model G3 verified the backend accepts
  python3 gates/G2/record.py --requires-g3              exit 0 only if G3 is a current PASS
  python3 gates/G2/record.py --preflight <obs> [<work>] exit 0 only if the shared state holds
  python3 gates/G2/record.py <obs> [<work>]             write gates/G2.json
"""

import datetime
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "gates"))

import codex_common as common  # noqa: E402

WORK = os.path.join(ROOT, "gates", "G2", "work")
EVIDENCE = os.path.join(ROOT, "gates", "G2.json")
G3_EVIDENCE = os.path.join(ROOT, "gates", "G3.json")
VERSIONS = os.path.join(ROOT, "runtime", "versions.yaml")

GATE = "G2"
SLOTS = ("a", "b")
SANDBOXES = {"a": "dca-g2-a", "b": "dca-g2-b"}
MARKER = "DCA-G2-OK"
CREDENTIAL = common.CREDENTIAL_FILE

PROXY_MANAGED = "proxy-managed"
TOKEN_FILE_TRUSTED_ONLY = "token-file-trusted-only"

# The patterns whose presence means REAL OpenAI OAuth material is readable. Kept in step with
# gates/G2/scan.py's TOKEN_MATERIAL; a field name in a template is not the same as a token.
TOKEN_MATERIAL = (
    "chatgpt-auth-store", "oauth-access-field", "oauth-refresh-field", "oauth-id-token-field",
    "oauth-access-camel", "oauth-refresh-camel", "jwt-material", "openai-project-key",
    "openai-api-key", "chatgpt-session-cookie",
)


def key_values(work, name):
    raw = common.read_file(work, name)
    if not isinstance(raw, str) or not raw.strip():
        return None
    out = {}
    for line in raw.splitlines():
        key, sep, value = line.strip().partition("=")
        if sep:
            out[key] = value
    return out or None


def events(work, name):
    raw = common.read_file(work, name)
    if not isinstance(raw, str) or not raw.strip():
        return None
    out = []
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if isinstance(event, dict) and isinstance(event.get("type"), str):
            out.append(event)
    return out or None


def answer_of(stream):
    """The model's answer, from streamed agent_choice deltas joined with no separator.

    The echoed prompt contains the marker, so reading it from any text in the document would make a
    run in which the model never answered look successful.
    """
    if not stream:
        return None
    parts = [e.get("content") for e in stream
             if e.get("type") == "agent_choice" and isinstance(e.get("content"), str)]
    return "".join(parts) if parts else None


def stream_model(stream):
    for event in stream or []:
        if event.get("type") != "team_info":
            continue
        for agent in event.get("available_agents") or []:
            if isinstance(agent, dict):
                return agent.get("provider"), agent.get("model")
    return None, None


def scan_results(work, name):
    """{pattern: total matches} and {pattern: [paths]} from one scan capture, or (None, None)."""
    raw = common.read_file(work, name)
    if not isinstance(raw, str) or not raw.strip():
        return None, None
    counts, paths, statuses = {}, {}, {}
    for line in raw.splitlines():
        fields = dict(part.split("=", 1) for part in line.split() if "=" in part)
        if line.startswith("scan ") and "pattern" in fields:
            try:
                counts[fields["pattern"]] = counts.get(fields["pattern"], 0) + int(
                    fields.get("matches", 0))
            except ValueError:
                continue
        elif line.startswith("scan ") and "status" in fields:
            statuses[fields.get("location", "?")] = fields["status"]
        elif line.startswith("hit ") and "pattern" in fields:
            paths.setdefault(fields["pattern"], []).append(fields.get("path", "?"))
    if not counts and not statuses:
        return None, None
    return {"counts": counts, "statuses": statuses}, paths


def g3_state(path=None, versions=None):
    """(the model G3 verified, why G3 may not be relied on).

    The path defaults LATE, to the module-level G3_EVIDENCE at call time, so the accepted evidence
    a caller points this at is the one actually read. A default bound at definition time silently
    ignores the caller's choice and reads the committed G3 instead, which would let a run report on
    an evidence document it never consulted.
    """
    if path is None:
        path = G3_EVIDENCE
    try:
        with open(path, encoding="utf-8") as fh:
            evidence = json.load(fh)
    except (OSError, ValueError):
        return None, "the G3 evidence could not be read"
    if not isinstance(evidence, dict) or evidence.get("gate") != "G3":
        return None, "the G3 evidence is not a G3 document"
    if evidence.get("status") != "PASS":
        return None, f"G3 is {evidence.get('status')!r}, not PASS, so T022 does not run"
    if versions is not None:
        stale = common.rules_module.evidence_problems(evidence, versions)
        if stale:
            return None, f"the G3 evidence is stale: {stale}"
    backend = evidence.get("codex_backend") or {}
    model = backend.get("selected_model")
    if not isinstance(model, str) or not model:
        return None, "G3 recorded no selected_model, so there is no verified model to run"
    return model, None


def evaluate(obs, work, versions=None, g4_path=None, g3_path=None):
    if versions is None:
        versions = common.read_versions(VERSIONS)
    if g4_path is None:
        g4_path = common.G4_EVIDENCE
    if g3_path is None:
        g3_path = G3_EVIDENCE
    rows = []

    # --- T022 runs only on a current G3 PASS ----------------------------------------------------------
    verified_model, g3_why = g3_state(g3_path, versions)
    rows.append((
        f"{GATE}.g3",
        "T022 runs only on a current G3 PASS, and it runs the model G3 proved the backend actually "
        "accepts - a stale or failed G3 is never interpreted as permission to proceed",
        g3_why is None,
        g3_why or f"G3 is a current PASS and verified the model {verified_model}",
        True,
    ))

    for row in common.preflight(GATE, obs, work, versions, g4_path):
        rows.append((*row, g3_why is None))
    preflight_ok = g3_why is None and all(row[2] for row in rows)

    base = common.base_identity_row(GATE, obs, work, versions)
    rows.append((*base, preflight_ok and "templates_exit" in obs))
    policy = common.policy_row(GATE, obs, "trusted", g4_path)
    rows.append((*policy, preflight_ok and "policy_allow" in obs))
    bindings_ok = preflight_ok and base[2] and policy[2]
    ran = bindings_ok and obs.get(f"create_{SANDBOXES['a']}_exit") == "0"

    vmstate = {slot: key_values(work, f"vmstate-{slot}.txt") for slot in SLOTS}
    streams = {slot: events(work, f"task-{slot}.json") for slot in SLOTS}
    answers = {slot: answer_of(streams[slot]) for slot in SLOTS}

    # --- NO credential file anywhere: without this the gate would prove nothing about the proxy --------
    absent_problems = []
    for slot in SLOTS:
        values = vmstate.get(slot) or {}
        if not values:
            absent_problems.append(f"{slot}: the VM state could not be read")
            continue
        if values.get("credential_files_found") != "0":
            absent_problems.append(
                f"{slot}: {values.get('credential_files_found')!r} copies of {CREDENTIAL} exist in "
                f"the VM ({values.get('credential_paths')!r}) - the proxy is not what authenticated "
                "this run")
        if values.get("config_dir_entries") != "0":
            absent_problems.append(
                f"{slot}: the config directory is not empty ({values.get('config_dir_entries')!r} "
                "entries)")
    rows.append((
        f"{GATE}.no-credential-file",
        f"{CREDENTIAL} exists NOWHERE in either sandbox and the config directory is empty, proven "
        "by searching the filesystem rather than by trusting that the gate skipped a copy - with "
        "the token-file fallback present, a successful run would say nothing about the proxy",
        not absent_problems,
        "; ".join(absent_problems) if absent_problems else
        f"a filesystem search found 0 copies of {CREDENTIAL} in both sandboxes and both config "
        "directories were empty",
        ran and "vmstate_a_exit" in obs,
    ))
    fixture_ok = ran and not absent_problems

    # --- did the native provider work on the proxy-managed credential alone? --------------------------
    exec_problems = []
    for slot in SLOTS:
        before = common.read_json(work, f"ls-before-{slot}.json") or {}
        if isinstance(before, dict) and before.get("sandboxes"):
            exec_problems.append(
                f"{slot}: a sandbox already existed when this one was created, so more than one was "
                "live at a time")
        if obs.get(f"create_{SANDBOXES[slot]}_exit") != "0":
            exec_problems.append(
                f"{slot}: create exited {obs.get(f'create_{SANDBOXES[slot]}_exit')!r}")
        if obs.get(f"task_{slot}_exit") != "0":
            exec_problems.append(
                f"{slot}: the native task exited {obs.get(f'task_{slot}_exit')!r}")
        elif MARKER not in (answers[slot] or ""):
            exec_problems.append(f"{slot}: the model did not answer with {MARKER}")
        provider, model = stream_model(streams[slot])
        if provider is not None and provider != common.PROVIDER:
            exec_problems.append(f"{slot}: the run reported provider {provider!r}")
        if verified_model and model is not None and model != verified_model:
            exec_problems.append(
                f"{slot}: the run reported model {model!r}, not the G3-verified {verified_model!r}")
        if (vmstate.get(slot) or {}).get("stdin_is_tty") != "no":
            exec_problems.append(f"{slot}: stdin was a TTY, so the run was not non-interactive")
    between = common.read_json(work, "ls-between-a.json") or {}
    if isinstance(between, dict) and between.get("sandboxes"):
        exec_problems.append("the first sandbox survived its removal, so the second was not fresh")
    rows.append((
        f"{GATE}.proxy-execution",
        "the native chatgpt provider ran the task successfully and non-interactively in TWO "
        "CONSECUTIVE completely fresh sandboxes holding no credential file, using the model G3 "
        "verified, with only one sandbox live at a time - so host-side proxy-managed OAuth is what "
        "authenticated it",
        not exec_problems,
        "; ".join(exec_problems) if exec_problems else
        f"both sandboxes answered {MARKER} on the proxy-managed credential alone, provider "
        f"{common.PROVIDER} model {verified_model}, stdin not a TTY",
        fixture_ok and "task_a_exit" in obs,
    ))
    execution_ok = fixture_ok and not exec_problems

    # --- the scanner can find a token when one is really there ----------------------------------------
    canary_problems = []
    canary_detail = []
    # Did the control demonstrably validate the scanner? This is tracked separately from the
    # criterion chain: when an earlier step fails closed the criterion is NOT-RUN, but the scan
    # itself still ran, and whether it ran is a question about the capture, not about the verdict.
    canary_validated = True
    for slot in SLOTS:
        if obs.get(f"scan_canary_{slot}_exit") != "0":
            canary_problems.append(
                f"{slot}: the canary scan exited "
                f"{obs.get(f'scan_canary_{slot}_exit', 'not reported')!r}, so the control did not "
                "complete")
            canary_validated = False
        result, _ = scan_results(work, f"scan-canary-{slot}.txt")
        if result is None:
            canary_problems.append(f"{slot}: the canary scan could not be read")
            canary_validated = False
            continue
        bad = [loc for loc, status in result["statuses"].items() if status != "ok"]
        if bad:
            canary_problems.append(f"{slot}: the canary scan reported status {bad}")
            canary_validated = False
        detected = [p for p in TOKEN_MATERIAL if result["counts"].get(p, 0) > 0]
        if not detected:
            canary_validated = False
            canary_problems.append(
                f"{slot}: the scanner did NOT detect the synthetic canary, so a clean result from it "
                "would be meaningless")
        else:
            canary_detail.append(f"{slot}: {len(detected)} pattern(s) fired ({', '.join(detected)})")
    rows.append((
        f"{GATE}.canary",
        "the scanner demonstrably detects OpenAI OAuth token material: a clearly synthetic, "
        "self-labelled canary is planted and found in each sandbox BEFORE the real scan, then "
        "removed so it cannot inflate it. Without this control a scanner that matched nothing would "
        "look exactly like a perfectly isolated VM",
        not canary_problems,
        "; ".join(canary_problems) if canary_problems else "; ".join(canary_detail),
        execution_ok and "scan_canary_a_exit" in obs,
    ))
    canary_ok = execution_ok and not canary_problems

    # --- is real token material readable by a privileged workload? ------------------------------------
    readable = {}
    scan_problems = []
    # Structural completeness ONLY: whether every location in both sandboxes was actually searched.
    # Finding material is a result, not a failure to observe, so it does not clear this flag.
    scan_completed = True
    for slot in SLOTS:
        # A scan that did not complete is not a clean scan. The command also exits non-zero when it
        # refused to run because the canary survived, and an unfinished privileged scan reporting
        # "no token material" is precisely the false negative this gate exists to prevent.
        if obs.get(f"scan_{slot}_exit") != "0":
            scan_problems.append(
                f"{slot}: the privileged scan exited "
                f"{obs.get(f'scan_{slot}_exit', 'not reported')!r}, so it never completed and can "
                "never read as clean")
            scan_completed = False
        result, paths = scan_results(work, f"scan-{slot}.txt")
        if result is None:
            scan_problems.append(f"{slot}: the scan could not be read")
            scan_completed = False
            continue
        bad = [loc for loc, status in result["statuses"].items() if status != "ok"]
        if bad:
            scan_problems.append(f"{slot}: the scan reported status {bad} for some locations")
            scan_completed = False
        for pattern in TOKEN_MATERIAL:
            hits = result["counts"].get(pattern, 0)
            if hits:
                readable.setdefault(pattern, 0)
                readable[pattern] += hits
                scan_problems.append(
                    f"{slot}: {pattern} x{hits} at {sorted(set((paths or {}).get(pattern, [])))[:3]}")
    rows.append((
        f"{GATE}.token-material",
        "no real OpenAI OAuth token material is readable by a PRIVILEGED (sudo) repository workload "
        "in either sandbox: the proxy keeps the credential on the host and the VM sees only a "
        "sentinel. Findings are recorded as pattern ids, counts and paths - never a value, a partial "
        "value, a digest, a header or a cookie",
        not scan_problems,
        "; ".join(scan_problems) if scan_problems else
        f"none of {len(TOKEN_MATERIAL)} token-material patterns fired in either sandbox, across "
        "home, etc, tmp, run, the process environment and /proc/*/environ",
        canary_ok and "scan_a_exit" in obs,
    ))
    isolation_ok = canary_ok and not scan_problems

    # --- strict, and the pinned binary ----------------------------------------------------------------
    strict_problems = []
    if obs.get("safety_flag") != common.REQUIRED_SAFETY:
        strict_problems.append(
            f"the gate recorded safety flag {obs.get('safety_flag')!r}, not "
            f"{common.REQUIRED_SAFETY!r}")
    rows.append((
        f"{GATE}.strict",
        "every native invocation passed --safety strict on the command line rather than relying on "
        "a config default; no weaker mode was substituted",
        not strict_problems,
        "; ".join(strict_problems) if strict_problems else
        f"--safety {common.REQUIRED_SAFETY} on every invocation",
        ran and "safety_flag" in obs,
    ))

    pin_problems = []
    for slot in SLOTS:
        observed = (vmstate.get(slot) or {}).get("agent_binary")
        if observed != versions.get("docker_agent"):
            pin_problems.append(
                f"{slot}: the in-VM docker-agent reported {observed!r}, not the pinned "
                f"{versions.get('docker_agent')!r}")
    rows.append((
        f"{GATE}.pinned-binary",
        "the docker-agent that actually ran in both sandboxes is the pinned artifact, reported "
        "after its startup self-update attempt",
        not pin_problems,
        "; ".join(pin_problems) if pin_problems else
        f"both sandboxes ran the pinned docker-agent {versions.get('docker_agent')}",
        ran and "vmstate_a_exit" in obs,
    ))

    fingerprint_row, before, _ = common.fingerprint_unchanged_row(GATE, obs, work)
    rows.append((*fingerprint_row, "policy_after_exit" in obs))
    rows.append((*common.cleanup_row(GATE, obs, work, tuple(SANDBOXES.values())),
                 "ls_after_exit" in obs))

    return common.criteria_rows(rows), before, {
        "verified_model": verified_model,
        "g3_why": g3_why,
        "readable": readable,
        # The scan searched every location in both sandboxes AND the canary proved it can find
        # material. Only then is "nothing was readable" an OBSERVATION rather than an absence of
        # one; reporting an unobserved negative as observed is the same lie as a false PASS.
        "token_material_scanned": scan_completed and canary_validated,
        "isolation_ok": isolation_ok,
        "execution_ok": execution_ok,
        "api_key_classification": {
            name: sorted({(vmstate.get(s) or {}).get(f"apikey_{name}") for s in SLOTS})
            for name in common.API_KEY_NAMES
        },
    }


def record(obs_path, work=WORK, versions_path=VERSIONS, evidence_path=EVIDENCE):
    obs = common.read_observations(obs_path)
    versions = common.read_versions(versions_path)
    criteria, fingerprint, facts = evaluate(obs, work, versions)
    passed = all(row["result"] == "PASS" for row in criteria)
    # A G3 that is not a current PASS means T022 did not run at all, which is a different fact from
    # a mechanism this gate tested and rejected.
    if facts["g3_why"] is not None:
        status = "NOT-RUN"
    else:
        status = "PASS" if passed else "FAIL"

    if status == "PASS":
        mechanism = PROXY_MANAGED
        trusted = ("proxy-managed OAuth is the preferred mechanism for trusted runs: the token "
                   "stays on the host and the VM sees only a sentinel")
        untrusted = ("proxy-managed OAuth is also preferred for untrusted runs, but this gate does "
                     "NOT make Codex untrusted-eligible: G9 must still prove control-plane "
                     "capability non-usability")
    elif status == "NOT-RUN":
        mechanism = None
        trusted = untrusted = None
    else:
        mechanism = TOKEN_FILE_TRUSTED_ONLY
        trusted = ("trusted Codex continues on the chatgpt-auth.json fallback G3 already proved "
                   "non-interactive in two fresh sandboxes; the token file is readable by trusted "
                   "repository workloads in the VM, which is the documented accepted risk")
        untrusted = ("untrusted Codex remains BLOCKED: no harness: codex, no provider API key, and "
                     "the launcher refuses an untrusted Codex request under this mechanism")

    evidence = {
        "gate": GATE,
        "status": status,
        "run_at": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "versions": {
            "sbx": (common.read_json(work, "pf-version.json") or {}).get("client", {}).get("version")
            or None
        },
        "provenance": common.rules_module.evidence_provenance(GATE, versions),
        "criteria": criteria,
        "codex_backend": {
            "provider": common.PROVIDER,
            "selected_model": facts["verified_model"],
            "model_fallback_applied": False,
            "model_selection_reason": (
                "not selected here: G2 runs the model G3 verified the backend accepts"),
            "safety": common.REQUIRED_SAFETY,
            "token_file_fallback_proven": True,
            "credential_mechanism": mechanism,
            # null whenever the privileged scan did not run, which is the usual case when execution
            # fails first: reporting false there would claim the sandbox was searched and found
            # clean when nothing was ever searched.
            "proxy_managed_token_readable": (
                bool(facts["readable"]) if facts["token_material_scanned"] else None),
            "trusted_implication": trusted,
            "untrusted_implication": untrusted,
        },
        "fallback_applied": (
            None if status != "FAIL" else
            f"credential mechanism {TOKEN_FILE_TRUSTED_ONLY}"),
        "not_run_reason": facts["g3_why"],
        "notes": (
            "ChatGPT OAuth isolation and the V1 credential-mechanism selection, tested in TWO "
            "CONSECUTIVE completely fresh mountless sandboxes created with --skills off from the "
            "exact pinned sandbox_bases.codex base, under the network policy accepted G4 proved for "
            "codex/trusted, with only one sandbox live at a time and the model G3 verified the "
            "backend actually accepts. NO chatgpt-auth.json is provisioned, and that is proven by "
            "searching the whole filesystem for it rather than by trusting that the gate skipped a "
            "copy - with the token-file fallback present a successful run would say nothing about "
            "the proxy. The privileged scan reuses gates/G1b/scan.py's traversal UNCHANGED, so it "
            "cannot regress to the three defects that made an earlier scan lie: `~` resolving to "
            "ROOT's home under sudo, overlapping roots double-counting matches, and an "
            "Authorization: Bearer reference being read as inline material; only the patterns "
            "differ, and they are aimed at what the ChatGPT store actually holds - snake_case "
            "access_token, id_token and refresh_token, all JWT-shaped - established from the host "
            "file's STRUCTURE (key names, types and lengths) and never from its values. A clearly "
            "synthetic, self-labelled canary is planted and required to be DETECTED before the real "
            "scan, then removed so it cannot inflate it, because a scanner that matches nothing is "
            "otherwise indistinguishable from a perfectly isolated VM. A FAIL here is a legitimate "
            "outcome and never avoided by weakening the test: it selects "
            "token-file-trusted-only, keeps trusted Codex on G3's proven fallback and leaves "
            "untrusted blocked. credential_mechanism alone never makes Codex untrusted-eligible - "
            "G9 decides capability non-usability. No token value, partial value, digest, "
            "Authorization header, cookie or credential file content is ever read or recorded. "
            "Every rule this gate added carried --sandbox; the global fingerprint is captured with "
            "zero sandboxes before and after; sbx policy init, sbx reset and sbx rm --all are never "
            "called. Both sandboxes were removed by name."
        ),
    }
    if fingerprint is not None:
        evidence["network_policy_fingerprint"] = fingerprint
    with open(evidence_path, "w", encoding="utf-8") as fh:
        fh.write(json.dumps(evidence, indent=2, ensure_ascii=False) + "\n")
    return status, criteria


def main(argv):
    args = argv[1:]
    mode = args.pop(0) if args and args[0].startswith("--") else None

    if mode == "--policy":
        which = args[0] if args else ""
        allow, deny = common.codex_policy("trusted")
        emit = {"allow": allow, "deny": deny}.get(which)
        if emit is None:
            print("usage: record.py --policy allow|deny", file=sys.stderr)
            return 2
        print(",".join(emit))
        return 0

    if mode == "--model":
        model, why = g3_state(versions=common.read_versions(VERSIONS))
        if model is None:
            print(f"G2: no verified model: {why}", file=sys.stderr)
            return 1
        print(model)
        return 0

    if mode == "--requires-g3":
        model, why = g3_state(versions=common.read_versions(VERSIONS))
        if why is not None:
            print(f"G2: T022 does not run: {why}", file=sys.stderr)
            return 1
        print(f"G2: G3 is a current PASS with verified model {model}")
        return 0

    path = args[0] if args else None
    work = args[1] if len(args) > 1 else WORK
    if path is None or mode not in (None, "--preflight"):
        print("usage: python3 gates/G2/record.py "
              "[--policy|--model|--requires-g3|--preflight] <observations.env> [<work-dir>]",
              file=sys.stderr)
        return 2

    if mode == "--preflight":
        obs = common.read_observations(path)
        rows = common.preflight(GATE, obs, work, common.read_versions(VERSIONS))
        for identifier, description, result, observed in rows:
            print(f"  {identifier} {'PASS' if result else 'FAIL'} {description}")
            print(f"          observed: {observed}")
        return 0 if all(row[2] for row in rows) else 1

    status, criteria = record(path, work)
    print(f"G2: {status} ({EVIDENCE})")
    for row in criteria:
        print(f"  {row['id']:<26} {row['result']:<8} {row['description']}")
        print(f"          observed: {row['evidence_ref']}")
    return 0 if status == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
