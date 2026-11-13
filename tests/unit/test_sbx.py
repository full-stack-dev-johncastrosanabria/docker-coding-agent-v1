"""The sandbox inventory is external evidence, not an empty list when its shape is unknown."""

import importlib.util
import json
import sys
import unittest
from pathlib import Path
from unittest import mock

if "dca_sbx" not in sys.modules:
    spec = importlib.util.spec_from_file_location(
        "dca_sbx", Path(__file__).resolve().parents[2] / "src" / "dca" / "sbx.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules["dca_sbx"] = module
    spec.loader.exec_module(module)
Sbx, SbxError = sys.modules["dca_sbx"].Sbx, sys.modules["dca_sbx"].SbxError


class TestSandboxInventory(unittest.TestCase):
    def test_empty_inventory_is_an_explicit_sandboxes_list(self):
        sbx = Sbx()
        with mock.patch.object(sbx, "run", return_value=(0, '{"sandboxes": []}', "")):
            self.assertEqual(sbx.list_sandboxes(), [])

    def test_named_sandboxes_and_their_metadata_are_preserved(self):
        listing = [{"name": "dca-run", "status": "running"}, {"name": "other"}]
        sbx = Sbx()
        with mock.patch.object(sbx, "run", return_value=(0, json.dumps({"sandboxes": listing}), "")):
            self.assertEqual(sbx.list_sandboxes(), listing)

    def test_sbx_053_array_inventory_is_normalized_without_discarding_entries(self):
        sbx = Sbx()
        for listing in ([], [{"name": "dca-run", "status": "running"}]):
            with self.subTest(listing=listing), mock.patch.object(
                    sbx, "run", return_value=(0, json.dumps(listing), "")):
                self.assertEqual(sbx.list_sandboxes(), listing)

    def test_unknown_inventory_shapes_are_not_evidence_of_zero_sandboxes(self):
        documents = [None, {}, {"error": "not authenticated"}, {"sandboxes": None},
                     {"sandboxes": {}}, {"sandboxes": ""}, {"sandboxes": False},
                     {"sandboxes": [None]}, {"sandboxes": [{}]},
                     {"sandboxes": [{"name": ""}]}, {"sandboxes": [{"name": 12}]},
                     {"sandboxes": [], "error": "not authenticated"},
                     [None], [{}], [{"name": None}]]
        sbx = Sbx()
        for document in documents:
            with self.subTest(document=document), mock.patch.object(
                    sbx, "run", return_value=(0, json.dumps(document), "")):
                with self.assertRaises(SbxError):
                    sbx.list_sandboxes()


class TestKitHost(unittest.TestCase):
    pins = {"builder": "dca-kit", "buildkit_image": "moby/buildkit@sha256:" + "a" * 64}

    def test_daemon_failure_stops_before_inspecting_a_builder(self):
        for reply in ((1, "", "offline"), (0, "", ""), (127, "", "missing")):
            with self.subTest(reply=reply):
                run = mock.Mock(return_value=reply)
                self.assertIn("Docker daemon", sys.modules["dca_sbx"].kit_host_problems(
                    self.pins, run)[0])
                self.assertEqual(run.call_count, 1)

    def test_missing_wrong_driver_and_unpinned_builders_are_refused(self):
        for reply, reason in (((1, "", "missing"), "unavailable"),
                              ((0, "Driver: docker\n", ""), "docker-container"),
                              ((0, "Driver: docker-container\n", ""), "pinned BuildKit image")):
            with self.subTest(reply=reply):
                run = mock.Mock(side_effect=[(0, "29.9.3\n", ""), reply])
                self.assertIn(reason, sys.modules["dca_sbx"].kit_host_problems(self.pins, run)[0])
                self.assertEqual(run.call_args.args[0], ["docker", "buildx", "inspect", "dca-kit"])

    def test_healthy_pinned_builder_needs_no_host_mutation(self):
        run = mock.Mock(side_effect=[(0, "29.9.3\n", ""),
                                    (0, 'Driver: docker-container\nimage="' +
                                     self.pins["buildkit_image"] + '"\n', "")])
        self.assertEqual(sys.modules["dca_sbx"].kit_host_problems(self.pins, run), [])
        self.assertEqual(run.call_count, 2)


if __name__ == "__main__":
    unittest.main()
