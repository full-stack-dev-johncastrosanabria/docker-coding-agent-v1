#!/bin/sh
# CASE A: the supported explicit allow decision. Snake_case JSON on stdout, exit 0 (E18).
cat >/dev/null
printf '{"hook_specific_output":{"hook_event_name":"pre_tool_use","permission_decision":"allow"}}\n'
exit 0
