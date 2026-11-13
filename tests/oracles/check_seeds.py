"""Rebuild every fixture seed twice and require the same commit (tasks.md T107).

This uses the production builder, fixed metadata and all currently authored fixtures. It needs
only Python and Git; it never starts a sandbox or invokes a model. Run from any directory:

    python3 tests/oracles/check_seeds.py [fixture-glob]
"""

import sys
import tempfile
from pathlib import Path

from run_oracles import bench


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if len(argv) > 1:
        print("usage: check_seeds.py [fixture-glob]", file=sys.stderr)
        return 2
    fixtures = bench.discover(selection=argv[0] if argv else None)
    if not fixtures:
        print("no matching fixtures", file=sys.stderr)
        return 1
    failed = False
    for fixture in fixtures:
        with tempfile.TemporaryDirectory(prefix="dca-seed-check-") as directory:
            seed = str(Path(fixture["_dir"], fixture.get("seed", "seed")))
            first = bench.build_seed(seed, str(Path(directory, "first")))
            second = bench.build_seed(seed, str(Path(directory, "second")))
        if first != second:
            failed = True
            print(f"{fixture['id']}: FAIL {first} != {second}")
        else:
            print(f"{fixture['id']}: OK {first}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
