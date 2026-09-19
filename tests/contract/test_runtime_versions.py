"""runtime/versions.yaml is JSON-compatible YAML read with the stdlib json module (tasks.md T004).

V1 convention (tasks.md, Global constraints): every DCA-owned .yaml document that Python code
parses is valid JSON, so it is also valid YAML 1.2. It is read and written only with the
stdlib json module. No YAML package and no custom YAML parser is used.
"""

import json
import unittest
from pathlib import Path

VERSIONS = Path(__file__).resolve().parents[2] / "runtime" / "versions.yaml"


def canonical_file_text(obj):
    return json.dumps(obj, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


class RuntimeVersions(unittest.TestCase):
    def setUp(self):
        with open(VERSIONS, encoding="utf-8") as fh:
            self.versions = json.load(fh)  # raises if the file is not JSON-compatible YAML

    def test_is_read_with_stdlib_json(self):
        self.assertIsInstance(self.versions, dict)

    def test_is_in_canonical_file_form(self):
        self.assertEqual(VERSIONS.read_text(encoding="utf-8"), canonical_file_text(self.versions))

    def test_decided_pins(self):
        v = self.versions
        self.assertEqual(v["docker_agent"], "v1.136.0")
        self.assertEqual(v["docker_agent_config_version"], 15)
        self.assertEqual(v["harness_module"], "github.com/rumpl/harness@9376b9c76461")
        self.assertEqual(v["claude_code"]["tested"], "2.1.277")
        self.assertEqual(v["sbx"]["minimum"], "0.43.0")

    def test_gate_owned_pins_are_null_or_well_formed(self):
        # Filled only by their gates (G0: sbx/claude_code exact; G6: artifact and sandbox bases;
        # G1a confirms the Claude base). Until then they are null; they are never guessed.
        v = self.versions
        artifact = v["docker_agent_artifact"]
        self.assertEqual(set(artifact), {"url", "sha256", "verification"})
        if artifact["sha256"] is not None:
            self.assertRegex(artifact["sha256"], r"^[0-9a-f]{64}$")
        self.assertIn(artifact["verification"], (None, "publisher-checksum", "recorded-reproducibility-pin"))
        for pin in (v["claude_code"]["exact"], v["sbx"]["exact"]):
            self.assertTrue(pin is None or (isinstance(pin, str) and pin))
        self.assertEqual(set(v["sandbox_bases"]), {"claude", "codex"})
        for backend in v["sandbox_bases"].values():
            self.assertEqual(set(backend), {"base", "version"})
            for value in backend.values():
                self.assertTrue(value is None or (isinstance(value, str) and value))


if __name__ == "__main__":
    unittest.main()
