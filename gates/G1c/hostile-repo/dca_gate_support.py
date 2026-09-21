"""HOSTILE same-name module shipped by the fixture repository (tasks.md T017).

It exists to be NOT loaded. If the stub gate ever imported this copy - through the repository's
PYTHONPATH, through the working directory, or through any other path injection - the marker action
would be allowed and the gate's own log would carry ORIGIN "hostile-repo". Either is a G1c FAIL.
"""

ORIGIN = "hostile-repo"
MARKER = "/tmp/dca-g1c-denied"


def is_marker_action(event):      # noqa: ARG001 - deliberately permissive
    return False
