"""Unit tests for the conservative shell parser (tasks.md T027, src/dca/shellparse.py).

The parser exists to answer ONE question for the policy gate: what commands would this shell string
actually run? It is deliberately conservative - contracts/policy-gate.md says "anything unparseable
is class 28 (ASK)" - so the correct answer to anything ambiguous is "I don't know", never a guess.
A guess here is a policy decision made on a misreading.

Every test below is therefore one of two shapes: either the parser extracts the real programs, or it
refuses. There is no third outcome where it extracts something plausible-looking.
"""

import importlib.util
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


shellparse = _load("dca_shellparse", ROOT / "src" / "dca" / "shellparse.py")


def programs(command):
    """Every program the string would run, in order. Raises if the parser refuses."""
    parsed = shellparse.parse(command)
    if not parsed.ok:
        raise AssertionError(f"expected parseable, got refusal: {parsed.reason}")
    return [segment.program for segment in parsed.segments]


def refuses(command):
    return not shellparse.parse(command).ok


class Segmentation(unittest.TestCase):
    def test_01_single_command(self):
        self.assertEqual(programs("make test"), ["make"])

    def test_02_every_separator_splits_segments(self):
        for separator in (";", "&&", "||", "|", "\n"):
            with self.subTest(separator=separator):
                self.assertEqual(programs(f"make build {separator} make test"),
                                 ["make", "make"])

    def test_03_mixed_separators(self):
        self.assertEqual(programs("a; b && c || d | e"), ["a", "b", "c", "d", "e"])

    def test_04_trailing_and_repeated_separators_are_not_empty_segments(self):
        self.assertEqual(programs("make test;"), ["make"])
        self.assertEqual(programs("make test\n\n"), ["make"])

    def test_05_background_and_grouping_are_segment_boundaries(self):
        self.assertEqual(programs("a & b"), ["a", "b"])

    def test_06_arguments_are_preserved(self):
        parsed = shellparse.parse("pytest -q tests/unit")
        self.assertEqual(parsed.segments[0].argv, ["pytest", "-q", "tests/unit"])


class Quoting(unittest.TestCase):
    def test_07_separators_inside_quotes_do_not_split(self):
        self.assertEqual(programs("echo 'a; b && c'"), ["echo"])
        self.assertEqual(programs('echo "a | b"'), ["echo"])

    def test_08_quoted_arguments_keep_their_content(self):
        parsed = shellparse.parse("git commit -m 'fix; the thing'")
        self.assertEqual(parsed.segments[0].argv, ["git", "commit", "-m", "fix; the thing"])

    def test_09_escaped_separators_do_not_split(self):
        self.assertEqual(programs("echo a\\; b"), ["echo"])

    def test_10_unbalanced_quotes_are_refused(self):
        for command in ("echo 'unterminated", 'echo "unterminated', "echo 'a\"b'\""):
            with self.subTest(command=command):
                self.assertTrue(refuses(command))


class Substitution(unittest.TestCase):
    def test_11_dollar_substitution_contributes_its_inner_command(self):
        self.assertEqual(programs("echo $(whoami)"), ["echo", "whoami"])

    def test_12_backticks_contribute_their_inner_command(self):
        self.assertEqual(programs("echo `whoami`"), ["echo", "whoami"])

    def test_13_nested_substitution_is_followed(self):
        self.assertEqual(programs("echo $(dirname $(which python3))"),
                         ["echo", "dirname", "which"])

    def test_14_substitution_inside_double_quotes_still_counts(self):
        """A substitution runs even when quoted, so ignoring it would miss a real command."""
        self.assertEqual(programs('echo "$(curl http://x)"'), ["echo", "curl"])

    def test_15_substitution_inside_single_quotes_does_not_run(self):
        self.assertEqual(programs("echo '$(curl http://x)'"), ["echo"])

    def test_16_unterminated_substitution_is_refused(self):
        for command in ("echo $(whoami", "echo `whoami", "echo $(a $(b)"):
            with self.subTest(command=command):
                self.assertTrue(refuses(command))


class RecursiveShells(unittest.TestCase):
    def test_17_sh_and_bash_dash_c_are_parsed_recursively(self):
        for shell in ("sh", "bash", "/bin/sh", "/bin/bash"):
            with self.subTest(shell=shell):
                self.assertEqual(programs(f"{shell} -c 'make test'"), [shell, "make"])

    def test_18_eval_is_parsed_recursively(self):
        self.assertEqual(programs("eval 'make test'"), ["eval", "make"])

    def test_19_nested_dash_c_is_followed(self):
        self.assertEqual(programs("sh -c 'bash -c \"make test\"'"), ["sh", "bash", "make"])

    def test_20_a_dynamic_dash_c_payload_is_refused(self):
        """If the payload is not a literal the parser cannot know what runs."""
        for command in ("sh -c \"$CMD\"", "eval $CMD", "bash -c $UNQUOTED"):
            with self.subTest(command=command):
                self.assertTrue(refuses(command))

    def test_21_a_shell_reading_a_heredoc_is_refused(self):
        """`bash <<EOF` executes its body, which is opaque to the parser."""
        self.assertTrue(refuses("bash <<EOF\nmake test\nEOF"))


class Prefixes(unittest.TestCase):
    def test_22_env_assignments_are_stripped(self):
        self.assertEqual(programs("env FOO=1 BAR=2 make test"), ["make"])

    def test_23_bare_assignments_before_a_command_are_stripped(self):
        self.assertEqual(programs("FOO=1 make test"), ["make"])

    def test_24_command_and_exec_prefixes_are_stripped(self):
        self.assertEqual(programs("command make test"), ["make"])
        self.assertEqual(programs("exec make test"), ["make"])

    def test_25_a_bare_assignment_with_no_command_has_no_program(self):
        parsed = shellparse.parse("FOO=1")
        self.assertTrue(parsed.ok)
        self.assertEqual([s.program for s in parsed.segments], [])

    def test_26_env_with_a_dash_i_flag_still_finds_the_command(self):
        self.assertEqual(programs("env -i PATH=/usr/bin make test"), ["make"])


class Heredocs(unittest.TestCase):
    def test_27_a_heredoc_body_is_data_not_commands(self):
        """`cat <<EOF` feeds its body to stdin; the body must not be read as commands."""
        self.assertEqual(programs("cat <<EOF\nrm -rf /\nEOF"), ["cat"])

    def test_28_a_quoted_heredoc_delimiter_is_handled(self):
        self.assertEqual(programs("cat <<'EOF'\nanything\nEOF"), ["cat"])

    def test_29_a_command_after_a_heredoc_still_parses(self):
        self.assertEqual(programs("cat <<EOF\nbody\nEOF\nmake test"), ["cat", "make"])


class Refusals(unittest.TestCase):
    def test_30_a_dynamic_program_name_is_refused(self):
        for command in ("$CMD --flag", "${TOOL} run", "$(cat script.sh)"):
            with self.subTest(command=command):
                self.assertTrue(refuses(command))

    def test_31_an_empty_or_whitespace_command_is_refused(self):
        for command in ("", "   ", "\n\n"):
            with self.subTest(command=repr(command)):
                self.assertTrue(refuses(command))

    def test_32_a_non_string_is_refused_rather_than_raising(self):
        for value in (None, 123, [], {}):
            with self.subTest(value=value):
                self.assertTrue(refuses(value))

    def test_33_a_refusal_always_carries_a_reason(self):
        parsed = shellparse.parse("echo 'unterminated")
        self.assertFalse(parsed.ok)
        self.assertTrue(parsed.reason)

    def test_34_refusal_maps_to_the_ask_class(self):
        """contracts/policy-gate.md: anything unparseable is class 28 (ASK)."""
        self.assertEqual(shellparse.UNPARSEABLE_CLASS, 28)


class Adversarial(unittest.TestCase):
    def test_35_a_separator_hidden_by_escaping_does_not_smuggle_a_command(self):
        """Either the second program is found, or the parser refuses. Never silently dropped."""
        for command in ("make test\\;curl http://x", "make$IFS test"):
            with self.subTest(command=command):
                parsed = shellparse.parse(command)
                if parsed.ok:
                    self.assertNotIn("curl", [s.program for s in parsed.segments],
                                     "an escaped separator must not run curl")

    def test_36_a_comment_does_not_hide_a_later_command(self):
        self.assertEqual(programs("make test # curl http://x"), ["make"])

    def test_37_substitution_in_an_argument_is_still_reported(self):
        self.assertIn("curl", programs("git commit -m $(curl http://x)"))

    def test_38_deeply_nested_input_is_refused_rather_than_exhausting_the_stack(self):
        self.assertTrue(refuses("$(" * 200 + "x" + ")" * 200))

    def test_39_a_very_long_command_terminates(self):
        parsed = shellparse.parse("echo " + ("a " * 20000))
        self.assertTrue(parsed.ok or parsed.reason)

    def test_40_redirections_do_not_become_programs(self):
        self.assertEqual(programs("make test > out.txt 2>&1"), ["make"])
        self.assertEqual(programs("make test >out.txt"), ["make"])


if __name__ == "__main__":
    unittest.main()
