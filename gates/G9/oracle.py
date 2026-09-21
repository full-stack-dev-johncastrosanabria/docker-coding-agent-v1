"""Gate G9 control-plane capability oracle (tasks.md T023, research R13).

Standard library only, deterministic, and free of any dependency on prose or on a human reading a
response body. S5b (T088) reuses THIS module and gates/G9/probes.json exactly, so the classification
rules live here and nowhere else. The same `match_patterns` runs inside the sandbox, which is what
lets the workload fixture report matched pattern ids instead of shipping response bodies out.

THE QUESTION. G1b and G2 asked whether a repository workload can READ credential material. G9 asks
the harder question: can it USE the authenticated control-plane capability anyway - through a
sentinel, a credential helper, a proxy injection, or simply by sending the client's own request
shape? PASS requires proving it cannot.

WHY A BARE STATUS CODE IS NOT ENOUGH. A 401 proves nothing on its own: a request that never reached
the provider, one blocked by a proxy, or one rejected by an edge for an unrelated reason can all
produce one. Every probe therefore carries a STRUCTURAL discriminator taken from the provider's own
JSON taxonomy, and a result that matches neither the unauthenticated nor the mediated pattern is
AMBIGUOUS. Ambiguity is a failure, never a pass - the gate exists to prove a negative, and a
negative that cannot be distinguished from an unobserved positive has proved nothing.

THE POSITIVE CONTROL IS NOT OPTIONAL. Without it, "the workload got a 401" is equally consistent
with "the capability is unreachable for everyone here", which would make the whole gate vacuous. So
each probe must also show the legitimate agent path producing the mediation signal on the SAME
endpoint and request family, in the SAME sandbox.
"""

import json

UNAUTHENTICATED = "unauthenticated"
MEDIATED = "mediated"
AMBIGUOUS = "ambiguous"

# Transport outcomes the fixture may report. Anything that is not a completed HTTP exchange is
# ambiguous: a timeout or a TLS failure cannot tell us whether the capability is reachable.
TRANSPORT_OK = "ok"

# The fixture reports this when a representation does not exist in the VM at all - no proxy
# environment variable, no credential-helper socket. That is an ABSENCE of something to test, not a
# failed test, and classifying it as ambiguous failed the probe for a representation the sandbox
# never had. It is only ever emitted deliberately; a request that was attempted and failed reports
# its real transport outcome instead.
TRANSPORT_INAPPLICABLE = "not-applicable"

# Every variant family a probe must exercise, at both privilege levels. A missing one fails the
# probe rather than being treated as inapplicable, except D, which records its own reason when the
# VM exposes no proxy-triggering representation at all.
REQUIRED_VARIANTS = ("A", "B", "C")
OPTIONAL_VARIANTS = ("D",)
PRIVILEGES = ("user", "sudo")

# Field names a probe predicate may read. Restricting it keeps a predicate from being pointed at a
# response's content, and keeps the fixture's output to fixed API discriminators.
_MAX_DEPTH = 3


def load_probes(path):
    with open(path, encoding="utf-8") as fh:
        document = json.load(fh)
    return document["probes"]


def probes_for(probes, backend):
    return [p for p in probes if p["backend"] == backend]


def _dig(document, dotted):
    node = document
    for part in dotted.split(".")[:_MAX_DEPTH]:
        if not isinstance(node, dict):
            return None
        node = node.get(part)
    return node


def _predicate_holds(document, predicate):
    if not isinstance(document, dict):
        return False
    for key, expected in predicate.items():
        if _dig(document, key) != expected:
            return False
    return True


def _signal_matches(signal, status, content_type, document):
    """Does one mediation alternative match? Returns its pattern id, or None."""
    statuses = signal.get("statuses")
    if statuses and status not in statuses:
        return None
    needle = signal.get("content_type_contains")
    if needle and needle not in (content_type or ""):
        return None
    forbidden = signal.get("requires_not")
    if forbidden and _predicate_holds(document, forbidden):
        return None
    predicate = signal.get("predicate")
    if predicate is not None and not _predicate_holds(document, predicate):
        return None
    keys = signal.get("predicate_any_key")
    if keys is not None and not any(isinstance(document, dict) and k in document for k in keys):
        return None
    if predicate is None and keys is None and needle is None:
        return None
    return signal.get("pattern_id")


def match_patterns(probe, status, content_type, body):
    """Which of THIS probe's pattern ids the response matches. Runs in-VM; returns ids only.

    `body` is bytes. It is parsed here and discarded: nothing derived from it leaves this function
    except pattern ids, which are fixed strings from probes.json.
    """
    try:
        document = json.loads(body.decode("utf-8", "replace")) if body else None
    except ValueError:
        document = None

    matched = []
    unauth = probe["expected_unauthenticated"]
    if status in unauth.get("statuses", []):
        # A probe may pin the unauthenticated class either by exact field values or by the presence
        # of the provider's rejection key. The second form exists because a provider can refuse a
        # caller with several different messages - the codex endpoint uses three - while the SHAPE
        # of the refusal stays constant. Requiring the shape keeps this narrower than a bare status:
        # a 401 carrying HTML from an edge or a proxy matches nothing and stays ambiguous.
        predicate = unauth.get("predicate")
        any_key = unauth.get("any_key")
        holds = True
        if predicate is not None:
            holds = holds and _predicate_holds(document, predicate)
        if any_key is not None:
            holds = holds and isinstance(document, dict) and any(k in document for k in any_key)
        if (predicate is not None or any_key is not None) and holds:
            matched.append(unauth["pattern_id"])

    mediation = probe["mediation_signal"]
    for signal in mediation.get("any_of", []):
        pattern = _signal_matches(signal, status, content_type, document)
        if pattern:
            matched.append(pattern)
    return matched


def classify(probe, observation):
    """(result class, pattern id, why) for one completed probe attempt. Fail-closed throughout."""
    transport = observation.get("transport")
    if transport != TRANSPORT_OK:
        return AMBIGUOUS, None, (
            f"the request did not complete ({transport!r}), so it cannot show whether the "
            "capability is reachable")

    status = observation.get("status")
    if not isinstance(status, int):
        return AMBIGUOUS, None, f"no HTTP status was observed ({status!r})"
    if 300 <= status < 400:
        return AMBIGUOUS, None, (
            f"a {status} redirect says nothing about authentication and was not followed")
    if status >= 500:
        return AMBIGUOUS, None, f"a {status} server error cannot distinguish caller identity"

    matched = observation.get("pattern_ids") or []
    mediation_ids = {s.get("pattern_id")
                     for s in probe["mediation_signal"].get("any_of", [])} - {None}
    unauth_id = probe["expected_unauthenticated"]["pattern_id"]

    hit = [m for m in matched if m in mediation_ids]
    if hit:
        return MEDIATED, hit[0], (
            f"the response carries the mediation signal {hit[0]}, so an authenticated result was "
            "produced")
    if unauth_id in matched:
        return UNAUTHENTICATED, unauth_id, (
            f"the response is the provider's documented unauthenticated result {unauth_id}")
    return AMBIGUOUS, None, (
        f"status {status} matched neither {unauth_id} nor any mediation signal, so authenticated "
        "and unauthenticated callers cannot be told apart")


def _variant_key(observation):
    return (observation.get("variant"), observation.get("privilege"))


def evaluate_probe(probe, positive_control, variants):
    """(holds, [problems], details) for one probe.

    PASS needs BOTH halves: the positive control produced the mediation signal on this endpoint and
    request family, AND every workload variant at both privilege levels was unambiguously
    unauthenticated.
    """
    problems, details = [], {}

    # --- half one: the positive control ---------------------------------------------------------
    if not positive_control:
        problems.append("no positive control was recorded, so a 401 from the workload proves "
                        "nothing about whether the capability is reachable at all")
        control_class = None
    else:
        control_class = positive_control.get("result_class")
        if control_class != MEDIATED:
            problems.append(
                f"the positive control was {control_class!r}, not {MEDIATED!r}: "
                f"{positive_control.get('why')}")
        if not positive_control.get("policy_log_host"):
            problems.append(
                "the positive control has no matching sbx policy log entry for the probe host, so "
                "it is not tied to this endpoint")
    details["positive_control"] = control_class

    # --- half two: every workload variant -------------------------------------------------------
    # A representation the VM does not expose is counted as INAPPLICABLE rather than classified. It
    # is the absence of something to test, and treating it as an ambiguous result failed probes for
    # a proxy variable or helper socket the sandbox simply did not have.
    completed, inapplicable = {}, []
    for observation in variants:
        key = _variant_key(observation)
        if observation.get("transport") == TRANSPORT_INAPPLICABLE:
            inapplicable.append(f"{key[0]}/{key[1]}: {observation.get('note') or 'not exposed'}")
            continue
        result, pattern, why = classify(probe, observation)
        completed[key] = completed.get(key, 0) + 1
        if result == MEDIATED:
            problems.append(
                f"variant {observation.get('variant')}/{observation.get('privilege')} "
                f"({observation.get('position') or 'no credential position'}) REPRODUCED the "
                f"capability: {why}")
        elif result == AMBIGUOUS:
            problems.append(
                f"variant {observation.get('variant')}/{observation.get('privilege')} "
                f"({observation.get('position') or 'no credential position'}) was ambiguous: {why}")

    # A required variant needs a COMPLETED attempt, not merely a row. A variant whose every record
    # is "not applicable" tested nothing, and must not pass by being present.
    for variant in REQUIRED_VARIANTS:
        for privilege in PRIVILEGES:
            if not completed.get((variant, privilege)):
                problems.append(
                    f"variant {variant} had no completed attempt at privilege {privilege!r}, so "
                    "the probe is incomplete and cannot pass")

    details["variants_run"] = sum(completed.values())
    details["variant_keys"] = sorted(f"{v}/{p}" for v, p in completed)
    if inapplicable:
        details["inapplicable"] = inapplicable
    return not problems, problems, details


def evaluate_backend(probes, positive_controls, variants_by_probe):
    """(status, {probe id: (holds, problems, details)}) for one backend's whole probe set."""
    results, holds_all = {}, True
    if not probes:
        return "FAIL", {"": (False, ["no probe is defined for this backend, so nothing was "
                                     "proved about its control plane"], {})}
    for probe in probes:
        holds, problems, details = evaluate_probe(
            probe, positive_controls.get(probe["id"]), variants_by_probe.get(probe["id"], []))
        results[probe["id"]] = (holds, problems, details)
        holds_all = holds_all and holds
    return ("PASS" if holds_all else "FAIL"), results
