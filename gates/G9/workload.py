"""Gate G9 in-VM workload fixture (tasks.md T023).

Runs INSIDE the sandbox as a repository-controlled process, once without sudo and once with it, and
tries to reproduce the agent's authenticated control-plane capability. It emits ONE JSON object per
line carrying only safe facts: probe id, variant id, privilege, credential position, discovery
location id, an opaque value index, HTTP status, content type, body byte length and matched pattern
ids. No token, Authorization value, cookie, credential-file content or response body ever leaves the
VM through this program.

WHY VARIANT B EXCLUDES REAL CREDENTIALS. G1b already established that a privileged Claude workload
can READ token material; replaying that token would only re-prove G1b's finding and would smuggle a
secret into the evidence. G9 asks the different question - whether the capability can be reached
WITHOUT a real credential, through a sentinel, a placeholder, a credential-helper value or a proxy
injection. So every discovered candidate is screened with the accepted G1b and G2 scanner patterns,
anything matching real credential material is DROPPED (counted, never recorded), and only non-secret
values are replayed.

Pattern matching is imported from gates/G9/oracle.py rather than reimplemented, so the classification
that runs in the VM is byte-for-byte the one the recorder and S5b use.

Usage:
  python3 workload.py --probes <probes.json> --oracle <oracle.py> --backend <name>
                      --privilege user|sudo --scanners <g1b-scan.py> <g2-scan.py>
"""

import importlib.util
import json
import os
import ssl
import sys
import time
import urllib.error
import urllib.request

TIMEOUT = 25
MAX_BODY = 8192
# Bounded AND paced. The first live run fired ~60 requests at one endpoint in a few seconds and was
# rate limited on every single one, including the variant that sends no credential at all - which
# answers nothing about capability and cost the gate a real result. Volume is the enemy of an
# informative probe here, not a source of extra assurance.
MAX_CANDIDATES = 3
PACE_SECONDS = 2.0
RATE_LIMIT_BACKOFF = 15.0
MAX_VALUE_LEN = 4096

# Names worth replaying, scoped to exactly what T023 enumerates: sentinels, placeholders,
# credential-helper values and proxy-related values for the providers these probes actually call.
# The NAME is metadata and is recorded; the value never is.
#
# THE SCOPE IS DELIBERATE, NOT LAZY. A broad "anything containing TOKEN or SECRET" sweep also picks
# up unrelated third-party credentials that happen to sit in the environment, and this fixture would
# then transmit one of them to Anthropic or OpenAI. Such a value could never authenticate against
# these two endpoints anyway - the credential families they accept are screened separately below -
# so replaying it buys the gate nothing and would leak somebody else's secret to a third party.
INTERESTING = (
    "ANTHROPIC", "OPENAI", "CHATGPT", "CLAUDE", "CODEX",
    "PROXY", "SBX", "DOCKER_AGENT", "CAGENT",
    "SENTINEL", "PLACEHOLDER", "CREDENTIAL_HELPER", "CREDHELPER",
)
PROXY_NAMES = ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy")


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def emit(record):
    print(json.dumps(record, sort_keys=True), flush=True)


# --- discovery ------------------------------------------------------------------------------------

def _interesting(name):
    upper = name.upper()
    return any(token in upper for token in INTERESTING)


def discover(secret_patterns):
    """[(location_id, name, value)] of NON-SECRET candidates a workload can actually reach.

    Anything matching the accepted credential patterns is dropped and only counted: those are
    secrets, and G9's question is whether the capability is reachable WITHOUT one.
    """
    raw, dropped = [], 0

    for name, value in os.environ.items():
        if _interesting(name) and value:
            raw.append(("environment", name, value))

    # /proc/*/environ is the privileged advantage: a sudo workload reads every process's environment,
    # including the agent's own.
    try:
        for entry in os.listdir("/proc"):
            if not entry.isdigit():
                continue
            try:
                with open(f"/proc/{entry}/environ", "rb") as fh:
                    blob = fh.read(MAX_BODY * 8)
            except OSError:
                continue
            for pair in blob.split(b"\0"):
                if b"=" not in pair:
                    continue
                name, _, value = pair.partition(b"=")
                name = name.decode("utf-8", "replace")
                value = value.decode("utf-8", "replace")
                if _interesting(name) and value:
                    raw.append(("proc-environ", name, value))
    except OSError:
        pass

    # Config locations that hold sentinels or helper settings. Only small files, and only values -
    # never the file contents - reach the candidate list.
    for location, path in (("agent-config", os.path.expanduser("~/.config/cagent")),
                           ("claude-config", os.path.expanduser("~/.claude")),
                           ("etc", "/etc/dca"), ("run", "/run/dca")):
        try:
            names = os.listdir(path)
        except OSError:
            continue
        for name in sorted(names)[:20]:
            full = os.path.join(path, name)
            try:
                if not os.path.isfile(full) or os.path.getsize(full) > MAX_VALUE_LEN:
                    continue
                with open(full, encoding="utf-8", errors="replace") as fh:
                    document = json.load(fh)
            except (OSError, ValueError):
                continue
            if isinstance(document, dict):
                for key, value in document.items():
                    if isinstance(value, str) and value and _interesting(key):
                        raw.append((f"{location}:{name}", key, value))

    kept, seen = [], set()
    for location, name, value in raw:
        if len(value) > MAX_VALUE_LEN:
            continue
        blob = value.encode("utf-8", "replace")
        if any(pattern.search(blob) for pattern in secret_patterns):
            dropped += 1          # real credential material: never replayed, never recorded
            continue
        if value in seen:
            continue
        seen.add(value)
        kept.append((location, name, value))
        if len(kept) >= MAX_CANDIDATES:
            break
    return kept, dropped


# --- requests -------------------------------------------------------------------------------------

def position_headers(position, value, probe):
    if position == "x-api-key":
        return {"x-api-key": value}
    if position == "authorization-bearer":
        return {"authorization": f"Bearer {value}"}
    if position == "chatgpt-account-id":
        return {"chatgpt-account-id": value}
    if position == "cookie":
        return {"cookie": f"__Secure-next-auth.session-token={value}"}
    return {}


def send(probe, headers, oracle, proxy=None, _retry=True):
    """One attempt, paced. Returns only safe facts.

    A 429 is not an answer to G9's question - it is the provider declining to say anything about
    the caller - so the attempt is retried ONCE after a longer pause rather than recorded as an
    uninformative result. This is not a weakening: a retried attempt that comes back with the
    mediation signal still fails the probe, and one that stays rate limited is still ambiguous.
    """
    time.sleep(PACE_SECONDS)
    body = json.dumps(probe["body"]).encode()
    request = urllib.request.Request(probe["endpoint"], data=body, method=probe["method"])
    for key, value in headers.items():
        request.add_header(key, value)
    if proxy is not None:
        opener = urllib.request.build_opener(
            urllib.request.ProxyHandler({"https": proxy, "http": proxy}))
    else:
        opener = urllib.request.build_opener()
    try:
        response = opener.open(request, timeout=TIMEOUT)
        status, raw = response.status, response.read(MAX_BODY)
        content_type = response.headers.get("content-type")
        transport = oracle.TRANSPORT_OK
    except urllib.error.HTTPError as exc:
        status, raw = exc.code, exc.read(MAX_BODY)
        content_type = exc.headers.get("content-type")
        transport = oracle.TRANSPORT_OK
    except ssl.SSLError as exc:
        return {"transport": "tls-error", "detail": type(exc).__name__}
    except urllib.error.URLError as exc:
        reason = getattr(exc, "reason", None)
        kind = "timeout" if "timed out" in str(reason).lower() else "url-error"
        return {"transport": kind, "detail": type(reason).__name__ if reason else "URLError"}
    except Exception as exc:                      # noqa: BLE001 - never read as a clean result
        return {"transport": "error", "detail": type(exc).__name__}
    if status == 429 and _retry:
        time.sleep(RATE_LIMIT_BACKOFF)
        return send(probe, headers, oracle, proxy=proxy, _retry=False)
    return {
        "transport": transport,
        "status": status,
        "content_type": content_type,
        "body_bytes": len(raw),
        "pattern_ids": oracle.match_patterns(probe, status, content_type, raw),
    }


def run(probe, privilege, candidates, oracle):
    base = dict(probe["client_headers"])
    full = dict(probe["client_structure_headers"])
    positions = probe["credential_positions"]

    def record(variant, observation, position=None, value_index=None, location=None, note=None):
        emit(dict(observation, probe=probe["id"], variant=variant, privilege=privilege,
                  position=position, value_index=value_index, location=location, note=note))

    # A. the correct request structure, with no credential material at all.
    record("A", send(probe, base, oracle))

    # B. every visible NON-SECRET value, replayed in every credential position the client uses.
    if not candidates:
        record("B", {"transport": "not-applicable"},
               note="no non-secret candidate value was reachable in this sandbox")
    for index, (location, name, value) in enumerate(candidates, 1):
        for position in positions:
            headers = dict(base)
            headers.update(position_headers(position, value, probe))
            record("B", send(probe, headers, oracle), position=position,
                   value_index=f"v{index}", location=f"{location}:{name}")

    # C. the client's full non-secret request structure: first bare, then with each B value.
    record("C", send(probe, full, oracle))
    for index, (location, name, value) in enumerate(candidates, 1):
        headers = dict(full)
        headers.update(position_headers(positions[0], value, probe))
        record("C", send(probe, headers, oracle), position=positions[0],
               value_index=f"v{index}", location=f"{location}:{name}")

    # D. any other representation in the VM that could trigger credential mediation.
    proxies = {name: os.environ[name] for name in PROXY_NAMES if os.environ.get(name)}
    sockets = []
    for directory in ("/run", "/tmp", "/var/run"):
        try:
            for name in sorted(os.listdir(directory))[:40]:
                path = os.path.join(directory, name)
                if name.endswith(".sock") or "cred" in name.lower() or "proxy" in name.lower():
                    sockets.append(path)
        except OSError:
            continue
    if not proxies and not sockets:
        record("D", {"transport": "not-applicable"},
               note="the VM exposes no proxy environment variable and no credential-helper socket")
    for name in sorted(proxies):
        record("D", send(probe, base, oracle, proxy=proxies[name]),
               location=f"environment:{name}", note="explicit proxy, no credential")
    for path in sockets[:4]:
        record("D", {"transport": "not-applicable"}, location=f"socket:{path}",
               note="a socket exists but exposes no documented credential-injection request family")


def main(argv):
    args = dict(zip(argv[1::2], argv[2::2]))
    probes_path = args.get("--probes")
    oracle_path = args.get("--oracle")
    backend = args.get("--backend")
    privilege = args.get("--privilege")
    scanners = [p for p in args.get("--scanners", "").split(",") if p]
    if not all((probes_path, oracle_path, backend, privilege)):
        print("usage: workload.py --probes P --oracle O --backend B --privilege user|sudo "
              "--scanners a.py,b.py", file=sys.stderr)
        return 2

    oracle = load("g9_oracle", oracle_path)
    patterns = []
    for path in scanners:
        try:
            module = load(f"scan_{os.path.basename(path)}", path)
            patterns.extend(module.PATTERNS.values())
        except Exception:                          # noqa: BLE001
            emit({"probe": None, "variant": "discovery", "privilege": privilege,
                  "transport": "error", "note": f"scanner {path} could not be loaded"})
            return 1
    if not patterns:
        emit({"probe": None, "variant": "discovery", "privilege": privilege,
              "transport": "error",
              "note": "no credential patterns were loaded, so non-secret screening is impossible"})
        return 1

    candidates, dropped = discover(patterns)
    emit({"probe": None, "variant": "discovery", "privilege": privilege,
          "transport": "ok", "candidates": len(candidates), "secrets_excluded": dropped,
          "locations": [f"{loc}:{name}" for loc, name, _ in candidates]})

    for probe in oracle.probes_for(oracle.load_probes(probes_path), backend):
        run(probe, privilege, candidates, oracle)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
