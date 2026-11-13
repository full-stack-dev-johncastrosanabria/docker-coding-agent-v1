"""Build-machine guard: a machine that builds this project must not hold a usable SSH agent.

Prints how many identities an SSH agent would offer and exits 1 when there is any. It reads no key
and prints no secret.
"""

import os
import subprocess
import sys


def usable_identities():
    if not os.environ.get("SSH_AUTH_SOCK"):
        return 0
    try:
        listed = subprocess.run(["ssh-add", "-l"], stdout=subprocess.PIPE,
                                stderr=subprocess.DEVNULL, timeout=10, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return 0
    return len(listed.stdout.splitlines()) if listed.returncode == 0 else 0


if __name__ == "__main__":
    count = usable_identities()
    print(f"ssh-agent identities usable here: {count}")
    sys.exit(1 if count else 0)
