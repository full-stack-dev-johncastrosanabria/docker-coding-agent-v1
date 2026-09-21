"""In-VM secret scanner for gate G2 (tasks.md T022): OpenAI/ChatGPT OAuth material.

Runs INSIDE the sandbox, under sudo. It emits ONLY pattern ids, location ids, a found/not-found
flag, a match COUNT and PATHS. It never emits a token value, never a partial value, never a
surrounding line, never a cookie, never an Authorization header, never the contents of a credential
file, and never a hash of a candidate - a hash of a real token is a verifier for anyone who later
guesses it. Nothing that could reconstruct a secret leaves the VM.

THE TRAVERSAL IS NOT REIMPLEMENTED. It is imported from gates/G1b/scan.py, which already fixes the
three defects that made an earlier scan lie: `~` resolving to ROOT's home under sudo (so the
workload's own credential store went unscanned and the VM looked isolated), overlapping roots
double-counting every match, and an `Authorization: Bearer` reference being reported as inline
material. Only the PATTERNS differ, because the credential family differs.

The pattern set targets what the ChatGPT credential store actually holds, established from the
host file's STRUCTURE - key names, value types and lengths, never values: snake_case access_token,
id_token and refresh_token, all JWT-shaped, alongside account_id, email, expires_at, last_refresh
and plan. A scanner aimed at the wrong shape cannot see the store it is pointed at and proves
nothing, which is exactly how G1b's first pass produced a false negative.

Usage (the caller passes G1b's scanner path so the shared logic is imported, not copied):
  sudo python3 scan.py --shared <path-to-g1b-scan.py> [<location>...]
"""

import importlib.util
import re
import sys

# OpenAI / ChatGPT credential shapes. Prefixes and field names are anchored; the bodies are not
# anchored to a length, so a format change still matches.
PATTERNS = {
    # The store's own fingerprint: these field names together identify the ChatGPT auth file even
    # if the token format changes. `last_refresh` is the distinctive one.
    "chatgpt-auth-store": re.compile(rb'"last_refresh"\s*:\s*"'),
    "chatgpt-account-id": re.compile(rb'"account_id"\s*:\s*"[^"]'),
    # The token fields themselves, snake_case as the store writes them, plus the camelCase variants
    # a different writer might use.
    "oauth-access-field": re.compile(rb'"access_token"\s*:\s*"[^"]'),
    "oauth-refresh-field": re.compile(rb'"refresh_token"\s*:\s*"[^"]'),
    "oauth-id-token-field": re.compile(rb'"id_token"\s*:\s*"[^"]'),
    "oauth-access-camel": re.compile(rb'"accessToken"\s*:\s*"[^"]'),
    "oauth-refresh-camel": re.compile(rb'"refreshToken"\s*:\s*"[^"]'),
    # The VALUE shape: every token in this store is a JWT, so the material is detectable even
    # outside a JSON field - in a log line, an env var or a process environment.
    "jwt-material": re.compile(rb"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\."),
    # OpenAI API-key families. V1 never introduces one, so a hit here is significant on its own.
    "openai-project-key": re.compile(rb"\bsk-proj-"),
    "openai-api-key": re.compile(rb"\bsk-[A-Za-z0-9]{24,}"),
    # Transport-level disclosure.
    "authorization-bearer": re.compile(rb"[Aa]uthorization\s*:\s*[Bb]earer\s+\S"),
    "chatgpt-session-cookie": re.compile(rb"__Secure-next-auth\.session-token\s*="),
}

# The patterns that mean REAL OpenAI OAuth token material is readable, as opposed to a field name
# appearing in a template or a reference. The gate's decision rests on this subset.
TOKEN_MATERIAL = (
    "chatgpt-auth-store", "oauth-access-field", "oauth-refresh-field", "oauth-id-token-field",
    "oauth-access-camel", "oauth-refresh-camel", "jwt-material", "openai-project-key",
    "openai-api-key", "chatgpt-session-cookie",
)


def load_shared(path):
    spec = importlib.util.spec_from_file_location("g1b_scan", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main(argv):
    args = argv[1:]
    if len(args) >= 2 and args[0] == "--shared":
        shared_path, locations = args[1], args[2:]
    else:
        print("usage: scan.py --shared <g1b-scan.py> [<location>...]", file=sys.stderr)
        return 2
    shared = load_shared(shared_path)
    # Swap ONLY the patterns. Every traversal, home-resolution and de-duplication rule stays as
    # G1b fixed it, so this scanner cannot regress to the bugs that made an earlier scan lie.
    shared.PATTERNS = PATTERNS
    only = locations or sorted(shared.LOCATIONS)
    for location in only:
        handler = shared.LOCATIONS.get(location)
        if handler is None:
            print(f"scan location={location} status=unknown-location")
            continue
        counts, paths = {}, {}
        try:
            handler(counts, paths)
            status = "ok"
        except Exception:                 # noqa: BLE001 - a failed scan must not read as clean
            status = "error"
        print(f"scan location={location} status={status}")
        for name in sorted(PATTERNS):
            hits = counts.get(name, 0)
            print(f"scan location={location} pattern={name} "
                  f"found={'yes' if hits else 'no'} matches={hits}")
            for where in sorted(paths.get(name, ()))[:10]:
                print(f"hit location={location} pattern={name} path={where}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
