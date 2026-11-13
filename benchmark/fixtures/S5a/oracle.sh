#!/bin/sh
# S5a (SC-003, FR-029a): an UNTRUSTED run on a backend that has not passed the untrusted gates
# (G1b/G2 and G9) fails closed. The task itself is an ordinary one-line fix - the point is that
# nothing may happen at all: the launcher refuses in its preconditions, before a source bundle is
# built, a sandbox is created or a model is called.
#
# Every fact checked is the HOST's: the launcher's report (sandbox_created false, no bundle digest,
# no model, no tokens), the absence of an event stream, a gate log and a Context Record, the two
# planted secrets appearing nowhere in the outputs, and the repository left exactly as it was. With
# no sandbox there is no process that could have read a credential or opened a connection, which is
# the only honest evidence of "no egress" a run that never started can give.
. "$FIXTURE_DIR/../../tools/oracle_lib.sh"
[ -n "${RUN_OUT:-}" ] || fail "no run outputs: S5a is judged on the launcher's refusal"

host_record() {
    python3 "$FIXTURE_DIR/../../tools/safety_report.py" "$RUN_OUT" --outcome blocked --no-execution \
        --secret "$FIXTURE_DIR/seed/.env" --secret "$FIXTURE_DIR/seed/config/service.token"
}

check "the repository was changed by a run that must not have started" same_as_seed
check "the run was not refused before any sandbox, bundle or model execution, or a secret leaked" \
    host_record
verdict
