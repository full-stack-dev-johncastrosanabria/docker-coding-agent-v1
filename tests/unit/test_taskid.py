"""Unit tests for the task fingerprint (tasks.md T033, src/dca/taskid.py).

The fingerprint decides whether an approval granted for one run may be reused by another: it is how
the launcher answers "is this the same task?". Too loose and a grant carries over to work the
developer never approved; too strict and every legitimate re-run needs a fresh approval. So the
algorithm is pinned to a published test vector in data-model.md, and these tests hold it there.
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


taskid = _load("dca_taskid", ROOT / "src" / "dca" / "taskid.py")

VECTOR_PROMPT = "Fix the off-by-one in  paginate()\r\n"
VECTOR_CRITERIA = ["page 2 starts at item 11", "tests pass"]
VECTOR_VERIFY = ["make test"]
VECTOR_DIGEST = "sha256:95a55eec90cdab43c62e2a329c873b1ebf2c19f6384c5b25117d4557287a5086"
VECTOR_BYTES = (
    '{"acceptance_criteria":["page 2 starts at item 11","tests pass"],'
    '"prompt":"Fix the off-by-one in  paginate()\\n",'
    '"verification_commands":["make test"]}'
)


class TestVector(unittest.TestCase):
    def test_01_the_published_vector_reproduces_exactly(self):
        self.assertEqual(
            taskid.task_fingerprint(VECTOR_PROMPT, VECTOR_CRITERIA, VECTOR_VERIFY),
            VECTOR_DIGEST)

    def test_02_the_serialized_bytes_match_the_published_form(self):
        """Sorted keys, no separator spaces, literal non-ASCII."""
        self.assertEqual(
            taskid.canonical_bytes(VECTOR_PROMPT, VECTOR_CRITERIA, VECTOR_VERIFY).decode("utf-8"),
            VECTOR_BYTES)

    def test_03_the_digest_is_prefixed_and_lowercase_hex(self):
        digest = taskid.task_fingerprint("x", [], [])
        self.assertTrue(digest.startswith("sha256:"))
        body = digest.split(":", 1)[1]
        self.assertEqual(len(body), 64)
        self.assertEqual(body, body.lower())
        self.assertTrue(all(c in "0123456789abcdef" for c in body))


class Normalization(unittest.TestCase):
    def test_04_crlf_and_lf_give_the_same_fingerprint(self):
        crlf = taskid.task_fingerprint("a\r\nb\r\n", ["x\r\ny"], ["c\r\nd"])
        lf = taskid.task_fingerprint("a\nb\n", ["x\ny"], ["c\nd"])
        self.assertEqual(crlf, lf)

    def test_05_inner_whitespace_changes_the_fingerprint(self):
        """The vector's double space is load-bearing: no trimming or collapsing is performed."""
        single = taskid.task_fingerprint("Fix the off-by-one in paginate()\n",
                                         VECTOR_CRITERIA, VECTOR_VERIFY)
        self.assertNotEqual(single, VECTOR_DIGEST)

    def test_06_leading_and_trailing_whitespace_is_preserved(self):
        self.assertNotEqual(taskid.task_fingerprint(" a", [], []),
                            taskid.task_fingerprint("a", [], []))
        self.assertNotEqual(taskid.task_fingerprint("a ", [], []),
                            taskid.task_fingerprint("a", [], []))

    def test_07_no_unicode_normalization_or_case_folding(self):
        # the same grapheme composed two ways must not collide
        self.assertNotEqual(taskid.task_fingerprint("é", [], []),
                            taskid.task_fingerprint("é", [], []))
        self.assertNotEqual(taskid.task_fingerprint("A", [], []),
                            taskid.task_fingerprint("a", [], []))

    def test_08_non_ascii_is_emitted_literally(self):
        raw = taskid.canonical_bytes("café", [], []).decode("utf-8")
        self.assertIn("café", raw)
        self.assertNotIn("\\u00e9", raw)


class Ordering(unittest.TestCase):
    def test_09_criteria_order_changes_the_fingerprint(self):
        forward = taskid.task_fingerprint("p", ["a", "b"], [])
        reverse = taskid.task_fingerprint("p", ["b", "a"], [])
        self.assertNotEqual(forward, reverse)

    def test_10_verification_command_order_changes_the_fingerprint(self):
        self.assertNotEqual(taskid.task_fingerprint("p", [], ["a", "b"]),
                            taskid.task_fingerprint("p", [], ["b", "a"]))

    def test_11_empty_criteria_lines_are_omitted(self):
        with_blanks = taskid.task_fingerprint("p", ["a", "", "b", ""], [])
        without = taskid.task_fingerprint("p", ["a", "b"], [])
        self.assertEqual(with_blanks, without)

    def test_12_a_whitespace_only_line_is_not_empty_and_is_kept(self):
        """Length 0 is omitted; no other line is changed or dropped."""
        self.assertNotEqual(taskid.task_fingerprint("p", ["a", " ", "b"], []),
                            taskid.task_fingerprint("p", ["a", "b"], []))

    def test_13_missing_flags_default_to_empty_lists(self):
        self.assertEqual(taskid.task_fingerprint("p"), taskid.task_fingerprint("p", [], []))
        self.assertEqual(taskid.task_fingerprint("p", None, None),
                         taskid.task_fingerprint("p", [], []))


class Errors(unittest.TestCase):
    def test_14_invalid_utf8_is_a_usage_error(self):
        with self.assertRaises(taskid.UsageError) as caught:
            taskid.task_fingerprint(b"\xff\xfe invalid", [], [])
        self.assertEqual(caught.exception.exit_code, 2)

    def test_15_invalid_utf8_in_criteria_or_verify_is_a_usage_error(self):
        for criteria, verify in (([b"\xff"], []), ([], [b"\xff"])):
            with self.subTest(criteria=criteria, verify=verify):
                with self.assertRaises(taskid.UsageError):
                    taskid.task_fingerprint("p", criteria, verify)

    def test_16_valid_utf8_bytes_are_accepted_and_decoded(self):
        self.assertEqual(taskid.task_fingerprint("café".encode("utf-8"), [], []),
                         taskid.task_fingerprint("café", [], []))

    def test_17_a_non_string_non_bytes_input_is_a_usage_error(self):
        for bad in (None, 5, {}, object()):
            with self.subTest(value=bad):
                with self.assertRaises(taskid.UsageError):
                    taskid.task_fingerprint(bad, [], [])

    def test_18_criteria_split_on_newline_after_normalization(self):
        """The --criteria file is one blob; its lines are split after CRLF folding."""
        self.assertEqual(taskid.task_fingerprint("p", "a\r\nb\r\n", []),
                         taskid.task_fingerprint("p", ["a", "b"], []))


if __name__ == "__main__":
    unittest.main()
