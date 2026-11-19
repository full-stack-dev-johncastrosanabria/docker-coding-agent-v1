"""A gate never inherits a TTY on stdin, so no sbx surface it uses can turn interactive.

Observed 2026-11-19 on sbx v0.54.0: with stdin a TTY and the global network policy
uninitialized, `sbx policy ls --json` prints a menu

    Choose the network policy for sandboxes
      1. Open ...  2. Balanced ...  3. Locked Down ...
    Choice [1-3, default 2]:

and blocks on input instead of reporting the uninitialized state. That hung G0 for 28
minutes at `gates/G0/run.sh` step 3, and the menu's default is the permissive `balanced`,
so a single keystroke would have set a global policy the gate never chose. With stdin
detached the same client exits 1 with the machine-readable text `record.py` matches byte
for byte, which is what authorizes the one-time `sbx policy init deny-all`.

These are Layer A tests: they read the scripts, run nothing. Constitution V and IX.
"""

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

#: The one line that makes every later invocation in a script non-interactive.
GUARD = "exec </dev/null"

#: A real invocation, not a mention: `sbx ...` as the command, in a pipeline, or substituted.
#: `echo "    sbx run --name $X"` (gates/G1a/run.sh) is instructions for the developer, not a call.
INVOCATION = re.compile(r"^\s*sbx\s+\S|\|\s*sbx\s+\S|\$\(\s*sbx\s+\S")


def gate_scripts():
    return sorted(ROOT.glob("gates/*/run.sh"))


def first_sbx_invocation(text):
    """The 1-based line number of the first real sbx invocation, or None."""
    for number, line in enumerate(text.splitlines(), 1):
        if line.lstrip().startswith("#"):
            continue
        if INVOCATION.search(line):
            return number
    return None


def guard_line(text):
    for number, line in enumerate(text.splitlines(), 1):
        if line.strip() == GUARD:
            return number
    return None


class GateStdin(unittest.TestCase):
    def test_there_are_gate_scripts_to_check(self):
        # Without this the suite would pass by finding nothing.
        self.assertGreater(len(gate_scripts()), 0, "no gates/*/run.sh found")

    def test_every_gate_that_invokes_sbx_detaches_stdin_first(self):
        checked = []
        for script in gate_scripts():
            text = script.read_text(encoding="utf-8")
            call = first_sbx_invocation(text)
            if call is None:
                continue
            relative = script.relative_to(ROOT)
            guard = guard_line(text)
            self.assertIsNotNone(
                guard,
                f"{relative} invokes sbx at line {call} but never runs `{GUARD}`: on a TTY an "
                "sbx prompt hangs the gate, and answering it can set global state the gate "
                "never chose",
            )
            self.assertLess(
                guard, call,
                f"{relative} runs `{GUARD}` at line {guard}, after its first sbx invocation at "
                f"line {call}: the calls before it can still prompt",
            )
            checked.append(str(relative))
        self.assertIn("gates/G0/run.sh", checked, "G0 invokes sbx and must be covered")
        self.assertIn("gates/G6/run.sh", checked, "G6 invokes sbx and must be covered")

    def test_the_detector_does_not_count_a_mention_as_an_invocation(self):
        self.assertIsNone(first_sbx_invocation('echo "    sbx run --name $LOGIN_SANDBOX"\n'))
        self.assertIsNone(first_sbx_invocation("# sbx policy ls --json is read-only\n"))
        self.assertEqual(first_sbx_invocation("set -u\nsbx version --json >out\n"), 2)
        self.assertEqual(first_sbx_invocation('x=$(sbx version --json)\n'), 1)
        self.assertEqual(first_sbx_invocation("true | sbx policy ls\n"), 1)

    def test_the_guard_is_found_only_on_its_own_line(self):
        self.assertEqual(guard_line(f"set -u\n{GUARD}\n"), 2)
        self.assertIsNone(guard_line(f"# {GUARD} would help here\n"))


if __name__ == "__main__":
    unittest.main()
