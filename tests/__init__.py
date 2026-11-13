import os

# The launcher's Claude auth precondition only checks that a `claude` CLI is on PATH. Appended, so
# a host that has the real CLI still finds it first; CI, which has none, finds the stand-in.
os.environ["PATH"] = os.pathsep.join(
    [os.environ.get("PATH", ""), os.path.join(os.path.dirname(__file__), "fakes", "claude")])
