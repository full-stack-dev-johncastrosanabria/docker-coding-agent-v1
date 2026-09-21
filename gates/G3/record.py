"""Evaluate G3 and write gates/G3.json (tasks.md T019): ChatGPT availability, the trusted
token-file fallback, and the native approval pipeline under --safety strict.

Standard library only. The shared preflight, accepted-G4 fingerprint, pinned-base verification,
provenance and cleanup rules come from gates/codex_common.py, which re-exports the already-reviewed
implementations rather than restating them.

Three properties:

  AVAILABILITY - the developer's ChatGPT sign-in drives the NATIVE Docker Agent chatgpt provider,
  with no harness and no provider API key. The model is not asserted from documentation: the gate
  reads the provider's own list and records what it selected. T019's fallback allows a different
  model only if it is a non-deprecated GPT-5.x; an older family never satisfies this gate.

  TRUSTED FALLBACK PROVISIONING - both of two CONSECUTIVE completely fresh sandboxes ran the task
  from a config directory holding ONLY chatgpt-auth.json. "Minimal" is checked, not asserted: the
  staged directory has exactly one entry, the in-VM config directory has exactly that one file, and
  the marker files of a full ~/.config/cagent copy (user-uuid, .cagent_tour) are absent.

  APPROVAL PIPELINE - under --safety strict the native pre_tool_use pipeline is fail-closed. The
  four cases are decided by whether the file was actually written, not by what the run reported:
  an explicit allow writes it, exit 2 does not, NO decision does not, and a user config asking for
  `safety: autonomous` + `yolo: true` does not. That last one is the strict proof: under autonomous
  every call is auto-approved and the hook is never consulted, so a hook that still blocks means
  the command line won.

REFRESH is OBSERVED, never assumed. `auth.openai.com` stays `host-oauth-login` /
`sandbox_required=false` unless the runs themselves show the sandboxed provider must refresh
against it. Both tasks succeeding while that host is explicitly DENIED to the sandbox is evidence
that in-VM refresh is NOT required, and the gate records it that way rather than promoting a host
because documentation mentions it.

The gate fails closed: an unreadable capture, a missing marker, a file that appears when it must
not, or a selection that is not on the provider's list is a failure, never a pass.

No token value, partial value, digest, Authorization header, cookie or credential file content is
ever read or recorded. Only presence, mode, owner, entry names and counts.

Usage:
  python3 gates/G3/record.py --policy allow|deny          emit the accepted G4 codex/trusted rules
  python3 gates/G3/record.py --select-model <models.txt>   print the model this gate must use
  python3 gates/G3/record.py --apikeys                    report provider API-key vars by name
  python3 gates/G3/record.py --preflight <obs> [<work>]   exit 0 only if the shared state holds
  python3 gates/G3/record.py <obs> [<work>]               write gates/G3.json
"""

import datetime
import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "gates"))

import codex_common as common  # noqa: E402

WORK = os.path.join(ROOT, "gates", "G3", "work")
EVIDENCE = os.path.join(ROOT, "gates", "G3.json")
VERSIONS = os.path.join(ROOT, "runtime", "versions.yaml")

GATE = "G3"
SLOTS = ("a", "b")
SANDBOXES = {"a": "dca-g3-a", "b": "dca-g3-b"}
MARKER = "DCA-G3-OK"
WRITE_MARKER = "DCA-G3-WRITE"

# T019's fallback is a NON-DEPRECATED GPT-5.x model. Nothing older, and never another provider.
GPT5_PATTERN = re.compile(r"^gpt-5(?:\.\d+)*(?:-[a-z0-9.]+)*$")
DEPRECATED_HINTS = ("deprecated", "legacy", "retired", "sunset")

# The approval matrix. `writes` is what the pipeline must actually do to the workspace file.
APPROVAL_CASES = (
    ("allow", True,
     "an explicit pre_tool_use allow decision lets the write execute, exactly once"),
    ("exit2", False,
     "a pre_tool_use hook that exits 2 blocks the call, and the file is never created"),
    ("no-decision", False,
     "a hook that runs and succeeds but expresses NO decision does not allow the call: absence of "
     "a decision falls through to confirmation, which --exec refuses"),
    ("hostile", False,
     "a user config asking for safety: autonomous and yolo: true does not defeat the command "
     "line: the session still runs strict, so the exit-2 hook is still consulted and still blocks"),
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
    """The typed events of one `docker agent run --exec --json` capture, or None."""
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
    """The model's answer, concatenated from its streamed agent_choice deltas.

    Deltas are joined with NO separator: one answer arrives as a sequence of fragments, so any
    separator makes a correct answer unrecognizable. The echoed prompt is never read as the answer.
    """
    if not stream:
        return None
    parts = [e.get("content") for e in stream
             if e.get("type") == "agent_choice" and isinstance(e.get("content"), str)]
    return "".join(parts) if parts else None


def parse_models(text):
    """[(provider, model, is_default)] from `docker agent models --provider chatgpt`."""
    out = []
    if not isinstance(text, str):
        return out
    for line in text.splitlines():
        fields = line.split()
        if len(fields) < 2 or fields[0].upper() == "PROVIDER":
            continue
        if fields[0] != common.PROVIDER:
            continue
        out.append((fields[0], fields[1], len(fields) > 2 and fields[2] == "*"))
    return out


def acceptable_models(models):
    """The models T019 permits: the GPT-5.x family, with any deprecated marking rejected."""
    out = []
    for provider, model, is_default in models:
        lowered = model.lower()
        if any(hint in lowered for hint in DEPRECATED_HINTS):
            continue
        if GPT5_PATTERN.match(lowered):
            out.append((provider, model, is_default))
    return out


def select_model(text):
    """(model, fallback_applied, why) - or (None, ...) when nothing acceptable is offered.

    The preferred model wins when the provider lists it. Otherwise T019's documented fallback picks
    the highest available non-deprecated GPT-5.x. A non-GPT-5 family is never selected just to let
    the gate pass, and no second provider is ever introduced.
    """
    models = parse_models(text)
    if not models:
        return None, None, "the provider listed no models at all"
    names = [m for _, m, _ in models]
    if common.PREFERRED_MODEL in names:
        return common.PREFERRED_MODEL, False, "the preferred model is listed"
    usable = acceptable_models(models)
    if not usable:
        return None, None, (f"the provider lists {names}, none of which is a non-deprecated "
                            f"GPT-5.x model, so T019's fallback has nothing to select")

    def version_key(name):
        return [int(p) for p in re.findall(r"\d+", name)]

    chosen = sorted((m for _, m, _ in usable), key=version_key)[-1]
    return chosen, True, (f"the preferred {common.PREFERRED_MODEL} is not listed; the documented "
                          f"fallback selected the non-deprecated GPT-5.x model {chosen} from "
                          f"{names}")


def runtime_models(work):
    """candidate -> exit code, from the in-VM enumeration. The RUNTIME is the authority."""
    return key_values(work, "model-probe.txt") or {}


def rejection_detail(work, candidate):
    """The backend's own refusal text for one candidate, or None. A message, never a secret."""
    raw = common.read_file(work, f"probe-{candidate}.err")
    if not isinstance(raw, str):
        return None
    match = re.search(r'"detail":"([^"]{0,200})"', raw)
    if match:
        return match.group(1)
    match = re.search(r"Error: ([^\n]{0,160})", raw)
    return match.group(1) if match else None


def select_runtime_model(work):
    """(model, fallback_applied, why) decided by what the BACKEND accepted, not by the listing.

    The candidates are tried in T019's preference order and enumeration stops at the first
    acceptance, so the first accepted candidate IS the highest available non-deprecated GPT-5.x.
    """
    probed = runtime_models(work)
    if not probed:
        return None, None, "no model was probed against the backend, so none is proven available"
    accepted = [c for c in common.MODEL_CANDIDATES if probed.get(c) == "0"]
    rejected = [c for c in common.MODEL_CANDIDATES if c in probed and probed.get(c) != "0"]
    if not accepted:
        return None, None, (f"the backend rejected every candidate it was asked about ({rejected}), "
                            "so no non-deprecated GPT-5.x model is usable")
    chosen = accepted[0]
    lowered = chosen.lower()
    if not GPT5_PATTERN.match(lowered) or any(h in lowered for h in DEPRECATED_HINTS):
        return None, None, (f"the backend accepted {chosen!r}, which is not a non-deprecated GPT-5.x "
                            "model, and T019 never selects an older family to make the gate pass")
    if chosen == common.PREFERRED_MODEL:
        return chosen, False, "the backend accepted the preferred model"
    why = (f"the preferred {common.PREFERRED_MODEL} is LISTED by `docker agent models` and marked "
           f"default, but the backend REJECTED it")
    detail = rejection_detail(work, common.PREFERRED_MODEL)
    if detail:
        why += f": {detail}"
    why += (f". T019's documented fallback selected {chosen}, the first candidate the backend "
            f"accepted in preference order; it also rejected {rejected}")
    return chosen, True, why


def stream_model(stream):
    """(provider, model) the run itself reported, from the event stream rather than from config."""
    for event in stream or []:
        if event.get("type") != "team_info":
            continue
        for agent in event.get("available_agents") or []:
            if isinstance(agent, dict):
                return agent.get("provider"), agent.get("model")
    return None, None


def api_key_report():
    """Provider API-key variables, by NAME. A value is never read."""
    return {name: bool(os.environ.get(name)) for name in common.API_KEY_NAMES}


def evaluate(obs, work, versions=None, g4_path=common.G4_EVIDENCE):
    if versions is None:
        versions = common.read_versions(VERSIONS)
    rows = []
    for row in common.preflight(GATE, obs, work, versions, g4_path):
        rows.append((*row, True))
    preflight_ok = all(row[2] for row in rows)

    base = common.base_identity_row(GATE, obs, work, versions)
    rows.append((*base, preflight_ok and "templates_exit" in obs))
    policy = common.policy_row(GATE, obs, "trusted", g4_path)
    rows.append((*policy, preflight_ok and "policy_allow" in obs))
    bindings_ok = preflight_ok and base[2] and policy[2]
    ran = bindings_ok and obs.get(f"create_{SANDBOXES['a']}_exit") == "0"

    vmstate = {slot: key_values(work, f"vmstate-{slot}.txt") for slot in SLOTS}
    streams = {slot: events(work, f"task-{slot}.json") for slot in SLOTS}
    answers = {slot: answer_of(streams[slot]) for slot in SLOTS}

    # --- an acceptable model, decided by the BACKEND rather than by the listing ------------------------
    listed = [m for _, m, _ in parse_models(common.read_file(work, "models.txt"))]
    selected, fallback, why = select_runtime_model(work)
    model_problems = []
    if obs.get("models_exit") != "0":
        model_problems.append(f"listing the provider's models exited {obs.get('models_exit')!r}")
    if selected is None:
        model_problems.append(why)
    elif obs.get("runtime_selected_model") != selected:
        model_problems.append(
            f"the gate recorded {obs.get('runtime_selected_model')!r} as accepted but the probe "
            f"results select {selected!r}")
    else:
        # The run must actually have used it. The event stream reports the effective provider and
        # model, so a config that drifted from the verified selection cannot pass unnoticed.
        for slot in SLOTS:
            provider, model = stream_model(streams[slot])
            if provider != common.PROVIDER:
                model_problems.append(
                    f"{slot}: the run reported provider {provider!r}, not the native "
                    f"{common.PROVIDER} provider")
            if model != selected:
                model_problems.append(
                    f"{slot}: the run reported model {model!r}, not the verified {selected!r}")
    rows.append((
        f"{GATE}.model",
        "the model this gate ran is one the BACKEND actually accepts, not merely one the provider "
        "lists: candidates from the pinned artifact are tried against the real endpoint in T019's "
        "preference order, the first acceptance is taken, it is a non-deprecated GPT-5.x, and both "
        "runs reported that exact model and the native chatgpt provider. An older family is never "
        "selected to make the gate pass and no second provider is introduced",
        not model_problems,
        "; ".join(model_problems) if model_problems else
        f"`docker agent models` lists {listed}; the backend accepted {selected} and rejected "
        f"{[c for c in common.MODEL_CANDIDATES if runtime_models(work).get(c) not in ('0', None)]}; "
        f"both runs reported provider {common.PROVIDER} model {selected}; fallback_applied "
        f"{bool(fallback)} - {why}",
        ran and "runtime_selected_model" in obs,
    ))
    model_ok = not model_problems

    # --- the run authenticated from the OAuth credential, not from any API key ------------------------
    # Two separate facts, because they are not the same thing. FIRST: V1 introduces no provider API
    # key - the host has none and this gate sets none. SECOND, and what actually matters: the run
    # DEPENDED on the credential file. A sandbox base may declare these variables itself, and an
    # empty or sentinel variable is not V1 introducing a key, so the decisive check is the negative
    # control - with the credential absent the identical task must fail. Values are never read: each
    # variable is classified absent / empty / present by name and length only.
    key_problems = []
    host_keys = key_values(work, "apikeys-host.txt") or {}
    for name, present in host_keys.items():
        if present not in ("absent", "False", "false"):
            key_problems.append(f"host: {name} is {present!r}")
    observed_env = {}
    for slot in SLOTS:
        values = vmstate.get(slot) or {}
        if not values:
            key_problems.append(f"{slot}: the VM state could not be read")
            continue
        for name in common.API_KEY_NAMES:
            observed_env.setdefault(name, set()).add(values.get(f"apikey_{name}"))
    control_exit = obs.get("control_nocred_exit")
    control_stream = events(work, "control-nocred.json")
    control_answer = answer_of(control_stream)
    if control_exit is None:
        key_problems.append("the no-credential negative control was never run, so nothing shows the "
                            "run depended on the OAuth credential rather than on the environment")
    elif control_exit == "0" or (control_answer and MARKER in control_answer):
        key_problems.append(
            f"THE NEGATIVE CONTROL SUCCEEDED: with an empty config directory the identical task "
            f"exited {control_exit!r} and the marker "
            f"{'appeared' if control_answer and MARKER in control_answer else 'did not appear'}, so "
            "something other than the OAuth credential can authenticate this provider")
    rows.append((
        f"{GATE}.no-api-key",
        "the run authenticated from the developer's subscription OAuth credential, not from a "
        "provider API key: the host has no API-key variable, this gate sets none, and the decisive "
        "negative control holds - with the credential absent the identical task fails. Variables the "
        "sandbox base declares itself are recorded by name and emptiness, never by value",
        not key_problems,
        "; ".join(key_problems) if key_problems else
        f"host: all absent; in-VM classification "
        f"{ {n: sorted(v)[0] if len(v) == 1 else sorted(v) for n, v in observed_env.items()} }; "
        f"negative control with no credential exited {control_exit!r} without the marker",
        ran and "vmstate_a_exit" in obs,
    ))

    # --- the pinned Docker Agent artifact is the one that ran ------------------------------------------
    pin_problems = []
    expected_binary = versions.get("docker_agent")
    for slot in SLOTS:
        observed = (vmstate.get(slot) or {}).get("agent_binary")
        if observed != expected_binary:
            pin_problems.append(
                f"{slot}: the in-VM docker-agent reported {observed!r}, not the pinned "
                f"{expected_binary!r}")
    rows.append((
        f"{GATE}.pinned-binary",
        "the docker-agent that actually ran in both sandboxes is the pinned artifact: the binary "
        "attempts a self-update at startup, so the version it reports afterwards is what proves the "
        "pin held rather than the kit's install step alone",
        not pin_problems,
        "; ".join(pin_problems) if pin_problems else
        f"both sandboxes ran the pinned docker-agent {expected_binary}",
        ran and "vmstate_a_exit" in obs,
    ))

    # --- the credential directory really was minimal -------------------------------------------------
    minimal_problems = []
    if obs.get("staged_entries") != "1":
        minimal_problems.append(
            f"the staged config directory held {obs.get('staged_entries')!r} entries, not exactly 1")
    if obs.get("staged_names") != common.CREDENTIAL_FILE:
        minimal_problems.append(
            f"the staged directory holds {obs.get('staged_names')!r}, not just "
            f"{common.CREDENTIAL_FILE}")
    for slot in SLOTS:
        values = vmstate.get(slot) or {}
        if not values:
            minimal_problems.append(f"{slot}: the VM state could not be read")
            continue
        if values.get("credential_present") != "yes":
            minimal_problems.append(f"{slot}: the credential was not present in the VM config dir")
        if values.get("config_dir_entries") != "1":
            minimal_problems.append(
                f"{slot}: the in-VM config dir holds {values.get('config_dir_entries')!r} entries, "
                f"not exactly 1 ({values.get('config_dir_names')!r})")
        if values.get("config_dir_names") != common.CREDENTIAL_FILE:
            minimal_problems.append(
                f"{slot}: the in-VM config dir holds {values.get('config_dir_names')!r}")
        if values.get("full_cagent_copied") != "no":
            minimal_problems.append(
                f"{slot}: markers of a full ~/.config/cagent copy are present in the VM config dir")
    rows.append((
        f"{GATE}.minimal-credential",
        "the trusted fallback copied ONLY chatgpt-auth.json: the staged directory and the in-VM "
        "config directory each hold exactly that one file, and the marker files of a full "
        "~/.config/cagent copy are absent, so no .env, cache or unrelated state travelled with it",
        not minimal_problems,
        "; ".join(minimal_problems) if minimal_problems else
        f"staged 1 entry ({common.CREDENTIAL_FILE}); both VM config dirs hold exactly it, mode "
        f"{(vmstate.get('a') or {}).get('credential_mode')} owner "
        f"{(vmstate.get('a') or {}).get('credential_owner')}; host ~/.config/cagent had "
        f"{obs.get('host_config_dir_entries')} entries and was not copied",
        ran and "staged_entries" in obs,
    ))

    # --- two consecutive completely fresh sandboxes, non-interactively --------------------------------
    fresh_problems = []
    for slot in SLOTS:
        before = common.read_json(work, f"ls-before-{slot}.json") or {}
        existing = before.get("sandboxes") if isinstance(before, dict) else None
        if existing:
            fresh_problems.append(
                f"{slot}: {len(existing)} sandbox(es) already existed when this one was created, so "
                "it was not created in a clean state and more than one was live at a time")
        created = obs.get(f"create_{SANDBOXES[slot]}_exit")
        if created != "0":
            fresh_problems.append(f"{slot}: create exited {created!r}")
        if obs.get(f"task_{slot}_exit") != "0":
            fresh_problems.append(f"{slot}: the native task exited {obs.get(f'task_{slot}_exit')!r}")
        if streams[slot] is None:
            fresh_problems.append(f"{slot}: the event stream could not be read")
        elif MARKER not in (answers[slot] or ""):
            fresh_problems.append(f"{slot}: the model did not answer with {MARKER}")
        values = vmstate.get(slot) or {}
        if values.get("stdin_is_tty") != "no":
            fresh_problems.append(f"{slot}: stdin was a TTY, so the run was not non-interactive")
        removal = obs.get(f"rm_{SANDBOXES[slot]}_exit")
        if removal != "0":
            fresh_problems.append(f"{slot}: removal exited {removal!r}")
    between = common.read_json(work, "ls-between-a.json") or {}
    if isinstance(between, dict) and between.get("sandboxes"):
        fresh_problems.append(
            "the first sandbox was still present after removal, so the second was not created from "
            "a clean state")
    rows.append((
        f"{GATE}.fresh-sandboxes",
        "TWO CONSECUTIVE completely fresh sandboxes each ran the native task successfully and "
        "non-interactively - stdin from /dev/null and not a TTY - with the second created only "
        "after the first was removed by name, so only one was ever live",
        not fresh_problems,
        "; ".join(fresh_problems) if fresh_problems else
        f"both sandboxes ({', '.join(SANDBOXES.values())}) answered {MARKER} with stdin not a TTY; "
        "each was created with zero sandboxes present and removed by name",
        ran and "task_a_exit" in obs,
    ))
    runs_ok = ran and not fresh_problems

    # --- the approval pipeline, decided by what happened to the file -----------------------------------
    approval_state = {name: key_values(work, f"approval-{name}.txt")
                      for name, _, _ in APPROVAL_CASES}
    for name, should_write, description in APPROVAL_CASES:
        problems = []
        values = approval_state.get(name)
        if obs.get(f"approval_{name}_exit") is None:
            problems.append("this approval case was never run")
        elif values is None:
            problems.append("the workspace state after this case could not be read")
        else:
            exists = values.get("file_exists")
            markers = values.get("marker_lines")
            if should_write:
                if exists != "yes":
                    problems.append("the allowed write did not happen: the file does not exist")
                elif markers != "1":
                    problems.append(
                        f"the file exists but carries {markers!r} marker lines, not exactly 1, so "
                        "the write did not happen exactly once")
            else:
                if exists != "no":
                    problems.append(
                        "THE BLOCKED WRITE HAPPENED: the file exists, so the pipeline failed open")
                elif markers not in ("0", None):
                    problems.append(f"the file is absent but {markers!r} marker lines were counted")
        rows.append((
            f"{GATE}.approval-{name}",
            description,
            not problems,
            "; ".join(problems) if problems else
            f"file_exists={(values or {}).get('file_exists')}, "
            f"marker_lines={(values or {}).get('marker_lines')} "
            f"(expected {'written once' if should_write else 'never written'})",
            runs_ok and f"approval_{name}_exit" in obs,
        ))

    # --- strict on the command line, and it actually held ----------------------------------------------
    strict_problems = []
    if obs.get("safety_flag") != common.REQUIRED_SAFETY:
        strict_problems.append(
            f"the gate recorded safety flag {obs.get('safety_flag')!r}, not "
            f"{common.REQUIRED_SAFETY!r}")
    hostile = approval_state.get("hostile") or {}
    if hostile.get("file_exists") != "no":
        strict_problems.append(
            "with a user config asking for autonomous + yolo, the blocked write happened, so the "
            "weaker config defeated the command line")
    setup = key_values(work, "approval-setup.txt") or {}
    if setup.get("hostile_config_safety") != "autonomous" or setup.get("hostile_config_yolo") != "true":
        strict_problems.append(
            f"the hostile user config did not actually ask for the weaker settings "
            f"(safety={setup.get('hostile_config_safety')!r}, yolo={setup.get('hostile_config_yolo')!r}), "
            "so this proves nothing about precedence")
    rows.append((
        f"{GATE}.strict",
        "every native invocation passed --safety strict on the command line rather than relying on "
        "a config default, and that mode actually held: the hostile user config really did ask for "
        "autonomous and yolo, and the hook was still consulted and still blocked. No weaker mode "
        "was substituted anywhere",
        not strict_problems,
        "; ".join(strict_problems) if strict_problems else
        f"--safety {common.REQUIRED_SAFETY} on every invocation; the hostile config asked for "
        f"safety={setup.get('hostile_config_safety')} yolo={setup.get('hostile_config_yolo')} and "
        "lost",
        runs_ok and "approval_hostile_exit" in obs,
    ))

    # --- refresh: OBSERVED, never assumed --------------------------------------------------------------
    refresh_state = common.refresh_candidate_state() or {}
    refresh_checks = [common.read_json(work, f"refresh-check-{slot}.json") for slot in SLOTS]
    denied = [isinstance(c, dict) and c.get("allowed") is False for c in refresh_checks]
    refresh_problems = []
    if refresh_state.get("purpose") != "host-oauth-login" or refresh_state.get("sandbox_required") is not False:
        refresh_problems.append(
            f"the committed inventory now classifies {common.REFRESH_CANDIDATE} as "
            f"{refresh_state.get('purpose')!r}/sandbox_required="
            f"{refresh_state.get('sandbox_required')!r}; a promotion must carry its own evidence_ref "
            "and re-run the affected G4 checks, so this gate cannot assume it")
    if not all(denied):
        refresh_problems.append(
            f"{common.REFRESH_CANDIDATE} was not denied to both sandboxes, so a successful run does "
            "not show that in-VM refresh is unnecessary")
    rows.append((
        f"{GATE}.refresh",
        f"whether the trusted token-file fallback needs {common.REFRESH_CANDIDATE} INSIDE the VM is "
        "recorded from runtime behavior, not from documentation: the host stays "
        "host-oauth-login/sandbox_required=false, it is explicitly denied to both sandboxes, and "
        "both native runs still succeeded - so no promotion is justified by this gate",
        not refresh_problems,
        "; ".join(refresh_problems) if refresh_problems else
        f"{common.REFRESH_CANDIDATE} denied to both sandboxes and both native runs succeeded "
        f"anyway; inventory still {refresh_state.get('purpose')}/sandbox_required="
        f"{refresh_state.get('sandbox_required')}, profiles {refresh_state.get('profiles')}",
        runs_ok and "refresh_check_a_exit" in obs,
    ))

    # --- the staged credential left no copy behind -----------------------------------------------------
    scrub_problems = []
    if obs.get("credentials_scrubbed") != "yes":
        scrub_problems.append(
            f"the staged credential directory was reported as {obs.get('credentials_scrubbed')!r}")
    rows.append((
        f"{GATE}.credential-hygiene",
        "the credential was staged only in a mode-0700 directory OUTSIDE the repository and that "
        "directory was removed on exit, so no copy of the token file remains in the tree and none "
        "of its contents, digests or headers was ever read or recorded",
        not scrub_problems,
        "; ".join(scrub_problems) if scrub_problems else
        "the staging directory was removed; evidence records only presence, mode, owner and entry "
        "names",
        ran and "staged_entries" in obs,
    ))

    fingerprint_row, before, _ = common.fingerprint_unchanged_row(GATE, obs, work)
    rows.append((*fingerprint_row, "policy_after_exit" in obs))
    rows.append((*common.cleanup_row(GATE, obs, work, tuple(SANDBOXES.values())),
                 "ls_after_exit" in obs))

    probed = runtime_models(work)
    return common.criteria_rows(rows), before, {
        "selected_model": selected, "fallback_applied": fallback, "why": why,
        "listed": listed,
        "accepted": [c for c in common.MODEL_CANDIDATES if probed.get(c) == "0"],
        "rejected": [c for c in common.MODEL_CANDIDATES if c in probed and probed.get(c) != "0"],
        "model_ok": model_ok, "refresh_state": refresh_state,
        "refresh_denied": all(denied) if refresh_checks else None,
        "runs_ok": runs_ok,
    }


def record(obs_path, work=WORK, versions_path=VERSIONS, evidence_path=EVIDENCE):
    obs = common.read_observations(obs_path)
    versions = common.read_versions(versions_path)
    criteria, fingerprint, facts = evaluate(obs, work, versions)
    status = "PASS" if all(row["result"] == "PASS" for row in criteria) else "FAIL"

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
            "selected_model": facts["selected_model"],
            "model_fallback_applied": bool(facts["fallback_applied"]),
            "model_selection_reason": facts["why"],
            "listed_models": facts["listed"],
            "backend_accepted_models": facts["accepted"],
            "backend_rejected_models": facts["rejected"],
            "safety": common.REQUIRED_SAFETY,
            "token_file_fallback_proven": status == "PASS",
            "in_vm_refresh_required": False if facts["refresh_denied"] else None,
            "refresh_candidate": facts["refresh_state"] or None,
        },
        "fallback_applied": (
            f"model {facts['selected_model']}" if facts["fallback_applied"] else None),
        "notes": (
            "Native ChatGPT availability and the trusted token-file fallback, proven in TWO "
            "CONSECUTIVE completely fresh mountless sandboxes created with --skills off from the "
            "exact pinned sandbox_bases.codex base, under the network policy accepted G4 proved for "
            "codex/trusted, with only one sandbox live at a time. The model is the one the "
            "signed-in plan actually lists, read from the provider rather than asserted; T019's "
            "fallback would accept only a non-deprecated GPT-5.x. Provisioning copies ONLY "
            "chatgpt-auth.json, never the full ~/.config/cagent: the staged directory and the "
            "in-VM config directory each hold exactly that one file and the markers of a full copy "
            "are absent. The credential is staged in a mode-0700 directory OUTSIDE the repository "
            "and removed on every exit path; no token value, partial value, digest, Authorization "
            "header, cookie or file content is ever read or recorded - only presence, mode, owner, "
            "entry names and counts. Every invocation passes --safety strict on the command line "
            "rather than relying on a config default. The approval matrix is decided by what "
            "happened to the workspace file, not by what the run reported: an explicit allow writes "
            "it exactly once, an exit-2 hook does not, a hook expressing NO decision does not, and "
            "a user config asking for safety: autonomous and yolo: true does not - which is the "
            "behavioral proof that strict held, because under autonomous the hook would never have "
            "been consulted. Whether the fallback needs auth.openai.com inside the VM is OBSERVED: "
            "that host is explicitly denied to both sandboxes and both runs still succeeded, so no "
            "promotion is justified here and it stays host-oauth-login/sandbox_required=false. "
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

    if mode == "--candidates":
        for candidate in common.MODEL_CANDIDATES:
            print(candidate)
        return 0

    if mode == "--select-model":
        if not args:
            return 2
        try:
            with open(args[0], encoding="utf-8") as fh:
                text = fh.read()
        except OSError as exc:
            print(f"the model list could not be read: {exc}", file=sys.stderr)
            return 1
        selected, _, why = select_model(text)
        if selected is None:
            print(f"G3: no acceptable model: {why}", file=sys.stderr)
            return 1
        print(selected)
        return 0

    if mode == "--apikeys":
        for name, present in api_key_report().items():
            print(f"{name}={'PRESENT' if present else 'absent'}")
        return 0

    path = args[0] if args else None
    work = args[1] if len(args) > 1 else WORK
    if path is None or mode not in (None, "--preflight"):
        print("usage: python3 gates/G3/record.py "
              "[--policy|--candidates|--select-model|--apikeys|--preflight] "
              "<observations.env> [<work-dir>]", file=sys.stderr)
        return 2

    if mode == "--preflight":
        obs = common.read_observations(path)
        rows = common.preflight(GATE, obs, work, common.read_versions(VERSIONS))
        for identifier, description, result, observed in rows:
            print(f"  {identifier} {'PASS' if result else 'FAIL'} {description}")
            print(f"          observed: {observed}")
        return 0 if all(row[2] for row in rows) else 1

    status, criteria = record(path, work)
    print(f"G3: {status} ({EVIDENCE})")
    for row in criteria:
        print(f"  {row['id']:<26} {row['result']:<8} {row['description']}")
        print(f"          observed: {row['evidence_ref']}")
    return 0 if status == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
