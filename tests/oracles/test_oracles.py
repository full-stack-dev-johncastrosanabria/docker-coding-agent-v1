"""Every benchmark oracle is sound on its golden patches (tasks.md T077).

The seed fails, `golden/good.patch` passes, and every `golden/bad/*.patch` fails - one subtest per
fixture, with no model and no sandbox. See run_oracles.py for why each case matters.
"""

import importlib.util
import shutil
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("dca_run_oracles", HERE / "run_oracles.py")
run_oracles = importlib.util.module_from_spec(spec)
sys.modules["dca_run_oracles"] = run_oracles
spec.loader.exec_module(run_oracles)


class TestOracles(unittest.TestCase):
    def test_every_oracle_rejects_the_seed_and_the_bad_patches_and_accepts_the_good_one(self):
        fixtures = run_oracles.bench.discover()
        self.assertTrue(fixtures)
        for fixture in fixtures:
            with self.subTest(fixture=fixture["id"]):
                if (Path(fixture["_dir"]) / "seed" / "package.json").exists() and not shutil.which(
                        "node"):
                    self.skipTest("node is not installed on this host")
                self.assertEqual(run_oracles.check(fixture), [])


if __name__ == "__main__":
    unittest.main()
