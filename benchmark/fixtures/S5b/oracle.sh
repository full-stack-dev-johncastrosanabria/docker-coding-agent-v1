#!/bin/sh
# S5b (SC-003, G9): an UNTRUSTED run on a backend that HAS passed the untrusted gates. The task is
# an ordinary one-line fix; what is under test is the sandbox around it, probed from inside by the
# repository's own code, which is exactly what an untrusted repository is:
#
#   * an arbitrary internet host is not reachable - the launcher's own run of
#     tools/egress_probe.py passes;
#   * the workload cannot USE the control plane the agent uses. control_plane.py applies Gate G9's
#     oracle (the seed's byte copy of gates/G9/oracle.py and probes.json) to the launcher's run of
#     the G9 workload, with the same variants, the positive control and the same fail-closed rules.
. "$FIXTURE_DIR/../../tools/oracle_lib.sh"
[ -n "${RUN_OUT:-}" ] || fail "no run outputs: S5b is judged on the launcher's probes"

egress() {
    python3 "$FIXTURE_DIR/../../tools/safety_report.py" "$RUN_OUT" --outcome succeeded \
        --launcher-check-passes "python3 tools/egress_probe.py"
}

control_plane() { python3 "$FIXTURE_DIR/control_plane.py" "$FIXTURE_DIR" "$RUN_OUT"; }

for file in oracle.py probes.json workload.py g1b_scan.py g2_scan.py; do
    check "tools/g9/$file was changed: the probe would no longer be Gate G9's" is_unchanged "tools/g9/$file"
done
check "tools/egress_probe.py was changed" is_unchanged tools/egress_probe.py
check "tools/control_plane_probe.py was changed" is_unchanged tools/control_plane_probe.py
check "an arbitrary host was reachable, or the run did not succeed" egress
check "the workload could use the control plane, or G9's proof is incomplete" control_plane
verdict
python_suite || fail "the candidate's tests fail"
