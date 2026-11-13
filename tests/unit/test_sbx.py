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


if __name__ == "__main__":
    unittest.main()
