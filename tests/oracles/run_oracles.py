"""Validate every benchmark oracle against its golden patches, with no model and no sandbox (T077).

For each fixture in benchmark/fixtures/:

  * the untouched seed FAILS the oracle - the task is not already solved;
  * `golden/good.patch` applied to the seed PASSES it;
  * every `golden/bad/*.patch` FAILS it - each is a plausible wrong answer (a weakened test, a
    partial fix, a workaround in the wrong place) the oracle must not accept.

Golden patches are repository-owned, so this runs the oracle on the host. Agent-produced
candidates never do: `dca bench` runs those in the pinned base image, offline.

Usage: python3 tests/oracles/run_oracles.py [fixture-glob]
"""

import importlib.util
import os
import shutil
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _load(name, path):
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


bench = _load("dca_bench", os.path.join(ROOT, "src", "dca", "bench.py"))


def candidate(fixture, patch, workdir):
    """The seed with `patch` applied (None: the seed itself), as a plain directory."""
    repo = os.path.join(workdir, "repo")
    shutil.rmtree(repo, ignore_errors=True)
    bench.build_seed(os.path.join(fixture["_dir"], fixture.get("seed", "seed")), repo)
    if patch is not None:
        proc = subprocess.run(["git", "-C", repo, "apply", "--index",
                               os.path.join(fixture["_dir"], patch)],
                              stdout=subprocess.PIPE, stderr=subprocess.STDOUT, check=False)
        if proc.returncode != 0:
            raise RuntimeError(f"{fixture['id']}: {patch} does not apply: "
                               f"{proc.stdout.decode('utf-8', 'replace').strip()}")
    return repo


def check(fixture):
    """Every violated expectation, as readable strings. Empty means the oracle is sound."""
    problems = []
    golden = fixture.get("golden") or {}
    cases = [(None, "fail")]
    if golden.get("good"):
        cases.append((golden["good"], "pass"))
    else:
        problems.append("no golden/good.patch")
    cases += [(path, "fail") for path in golden.get("bad") or []]
    if not golden.get("bad"):
        problems.append("no golden/bad/*.patch")
    workdir = tempfile.mkdtemp(prefix=f"dca-oracle-{fixture['id']}-")
    try:
        for patch, expected in cases:
            verdict, detail = bench.run_oracle_on_host(fixture, candidate(fixture, patch, workdir))
            if verdict != expected:
                problems.append(f"{patch or 'the seed'}: oracle {verdict}, expected {expected}"
                                f" ({detail[-300:]})")
    finally:
        shutil.rmtree(workdir, ignore_errors=True)
    return problems


def main(argv):
    selection = argv[1] if len(argv) > 1 else None
    failed = False
    for fixture in bench.discover(os.path.join(ROOT, "benchmark", "fixtures"), selection):
        problems = check(fixture)
        failed = failed or bool(problems)
        print(f"{fixture['id']}: {'OK' if not problems else 'FAIL'}")
        for problem in problems:
            print(f"  - {problem}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
