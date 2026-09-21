#!/bin/sh
# gates/G11/hooks/allow.sh - the explicit pre_tool_use ALLOW decision (research E18).
#
# WHY THIS EXISTS. Under `--safety strict` the native Codex approval pipeline is fail-closed: G3
# proved that a tool call with NO hook decision is REJECTED ("The user rejected the tool call"), so
# a strict Codex agent carrying no hook can dispatch exactly zero tool calls. G11-A's criterion 1
# needs N genuine tool calls to count, so without an explicit allow the criterion is not merely
# failed, it is unevaluable.
#
# This does not weaken the gate. `--safety strict` is still passed on the command line of every
# invocation and recorded in the evidence; this is the supported approval decision G3 case A already
# proved, and the hook is configured `on_error: block` so a hook that cannot run denies the call.
#
# Snake_case JSON on stdout, exit 0.
cat >/dev/null
printf '{"hook_specific_output":{"hook_event_name":"pre_tool_use","permission_decision":"allow"}}\n'
exit 0
