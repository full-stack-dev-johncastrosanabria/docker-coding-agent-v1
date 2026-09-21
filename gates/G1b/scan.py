"""In-VM secret scanner for gate G1b (tasks.md T021). Runs INSIDE the sandbox, under sudo.

It emits ONLY pattern ids, location ids, a found/not-found flag and a match COUNT. It never emits
a token value, never a partial value, never a surrounding line, never a cookie, never an
Authorization header, never the contents of a secret file, and never a hash of a candidate - a
hash of a low-entropy or guessable secret is itself a disclosure, and a hash of a real token is a
verifier for anyone who later guesses it. Nothing that could reconstruct a secret leaves the VM.

A NEGATIVE result is only meaningful if the scanner can find a token when one is really there, so
the caller plants a clearly-synthetic canary and the gate requires it to be detected. Without that
control, a scanner that silently matched nothing would look identical to a perfectly isolated VM.
"""

import os
import re
import sys

# Claude / Anthropic credential shapes. The prefixes are the documented key families; the trailing
# body is deliberately not anchored to a length, so a format change still matches the prefix.
PATTERNS = {
    "claude-oauth-access": re.compile(rb"sk-ant-oat[0-9]{2}-"),
    "claude-oauth-refresh": re.compile(rb"sk-ant-ort[0-9]{2}-"),
    "anthropic-api-key": re.compile(rb"sk-ant-api[0-9]{2}-"),
    "anthropic-admin-key": re.compile(rb"sk-ant-adm[0-9]{2}-"),
    "authorization-bearer": re.compile(rb"[Aa]uthorization\s*:\s*[Bb]earer\s+\S"),
    "claude-session-cookie": re.compile(rb"sessionKey\s*=\s*\S"),
    "oauth-refresh-field": re.compile(rb'"refresh_token"\s*:\s*"[^"]'),
    "oauth-access-field": re.compile(rb'"access_token"\s*:\s*"[^"]'),
    # The shapes the pinned Claude Code actually writes. The first scan matched none of the
    # patterns above although /home/agent/.claude/.credentials.json was present and 241 bytes
    # long, which is a FALSE NEGATIVE, not isolation: the store uses camelCase keys inside a
    # claudeAiOauth object rather than the snake_case OAuth field names. A scanner that cannot
    # see the credential store it is pointed at proves nothing.
    "claude-oauth-store": re.compile(rb'"claudeAiOauth"\s*:'),
    "oauth-access-camel": re.compile(rb'"accessToken"\s*:\s*"[^"]'),
    "oauth-refresh-camel": re.compile(rb'"refreshToken"\s*:\s*"[^"]'),
}

# An `Authorization: Bearer` match means opposite things depending on what follows it. A shell or
# template reference is plumbing; a literal is a disclosure. The distinction is recorded as a
# CLASSIFICATION - never the matched text.
BEARER_REFERENCE = re.compile(rb"[Aa]uthorization\s*:\s*[Bb]earer\s+[\$\{%<]")

MAX_BYTES = 2 * 1024 * 1024      # per file; a credential store is small, a log may not be
SKIP_DIRS = {"/proc", "/sys", "/dev"}


def scan_bytes(blob, counts, where=None, paths=None):
    """Count matches. `where` is a PATH, which T021 explicitly permits recording.

    A path is recorded so a hit can be adjudicated at all: a single match under /etc is a very
    different fact depending on whether it is a credential store or a vendored documentation
    sample, and a gate that cannot tell them apart either raises a false alarm or waves a real
    leak through. The path is metadata; the matching bytes are still never emitted.
    """
    for name, pattern in PATTERNS.items():
        found = len(pattern.findall(blob))
        if found:
            counts[name] = counts.get(name, 0) + found
            if paths is not None and where is not None:
                label = where
                if name == "authorization-bearer":
                    # Classification only: is the credential a reference, or inline?
                    kind = ("reference" if len(BEARER_REFERENCE.findall(blob)) >= found
                            else "literal")
                    label = f"{where} [{kind}]"
                paths.setdefault(name, set()).add(label)


def scan_tree(root, counts, paths=None):
    for dirpath, dirnames, filenames in os.walk(root, onerror=lambda _e: None):
        if any(dirpath == s or dirpath.startswith(s + "/") for s in SKIP_DIRS):
            dirnames[:] = []
            continue
        dirnames[:] = [d for d in dirnames if not os.path.islink(os.path.join(dirpath, d))]
        for name in filenames:
            path = os.path.join(dirpath, name)
            try:
                if os.path.islink(path) or not os.path.isfile(path):
                    continue
                with open(path, "rb") as fh:
                    scan_bytes(fh.read(MAX_BYTES), counts, path, paths)
            except (OSError, ValueError):
                continue


def scan_environment(counts, paths=None):
    for key, value in os.environ.items():
        # The NAME of the variable is metadata and is recorded; the value never is.
        scan_bytes(f"{key}={value}".encode("utf-8", "replace"), counts, f"env:{key}", paths)


def scan_proc_environ(counts, paths=None):
    try:
        entries = os.listdir("/proc")
    except OSError:
        return
    for entry in entries:
        if not entry.isdigit():
            continue
        try:
            with open(f"/proc/{entry}/environ", "rb") as fh:
                scan_bytes(fh.read(MAX_BYTES), counts, f"/proc/{entry}/environ", paths)
        except (OSError, ValueError):
            continue


def scan_homes(counts, paths=None):
    """Every user home, resolved explicitly.

    The scan runs under sudo, so os.path.expanduser("~") resolves to ROOT's home, not the
    workload's. Using it silently scanned /root and reported the agent's home clean while
    /home/agent/.claude/.credentials.json sat in it - a false negative that made the VM look more
    isolated than it is. T021's "~" means the workload's home, so the homes are named explicitly.
    """
    # /home already contains the workload's home, so adding ~$SUDO_USER as a second root would
    # scan the same files twice and DOUBLE every match count - an inflated count in the evidence
    # implies more occurrences than exist. Deduplicating whole roots is not enough, because one
    # root can be nested inside another; the two named here are disjoint.
    seen, roots = set(), ["/home", "/root"]
    for root in roots:
        real = os.path.realpath(root)
        if real in seen or not os.path.isdir(real):
            continue
        if any(real == s or real.startswith(s + "/") for s in seen):
            continue
        seen.add(real)
        scan_tree(real, counts, paths)


LOCATIONS = {
    "home": scan_homes,
    "etc": lambda c, p: scan_tree("/etc", c, p),
    "tmp": lambda c, p: scan_tree("/tmp", c, p),
    "run": lambda c, p: scan_tree("/run", c, p),
    "environment": scan_environment,
    "proc-environ": scan_proc_environ,
}


def main(argv):
    only = argv[1:] or sorted(LOCATIONS)
    for location in only:
        handler = LOCATIONS.get(location)
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
            # Counts only. No value, no excerpt, no offset, no digest.
            print(f"scan location={location} pattern={name} "
                  f"found={'yes' if hits else 'no'} matches={hits}")
            # Paths only, so a hit can be judged. Never the matching bytes.
            for where in sorted(paths.get(name, ()))[:10]:
                print(f"hit location={location} pattern={name} path={where}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
