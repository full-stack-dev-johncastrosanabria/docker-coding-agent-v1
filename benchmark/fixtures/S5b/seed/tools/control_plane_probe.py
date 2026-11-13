"""Run the Gate G9 workload (tools/g9, byte copies of gates/G9) as this repository's own process.

Once as the workload user and once under sudo, for the backend this sandbox runs. The workload
prints one JSON object per line and never a secret; whether the result is acceptable is decided
outside the sandbox, by the same G9 oracle, from these lines.
"""

import os
import subprocess
import sys

HERE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "g9")
BACKEND = "claude" if os.path.isdir("/etc/claude-code") else "codex"


def workload(privilege):
    argv = [sys.executable, os.path.join(HERE, "workload.py"),
            "--probes", os.path.join(HERE, "probes.json"), "--oracle", os.path.join(HERE, "oracle.py"),
            "--backend", BACKEND, "--privilege", privilege,
            "--scanners", ",".join(os.path.join(HERE, name) for name in ("g1b_scan.py", "g2_scan.py"))]
    return subprocess.run((["sudo", "-n"] if privilege == "sudo" else []) + argv, check=False).returncode


if __name__ == "__main__":
    sys.exit(1 if any([workload("user"), workload("sudo")]) else 0)
