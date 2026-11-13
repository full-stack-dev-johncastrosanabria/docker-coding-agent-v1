# Shared sandbox creation for every gate after G6 (sourced by gates/<ID>/run.sh). POSIX sh.
#
# sbx >= 0.53 resolves a bare agent name (`claude`) to the v3 workload kit's MUTABLE :latest, so a
# gate never names an agent: it creates from the digest-qualified workload G6 pinned in
# runtime/versions.yaml (`sandbox_bases.<backend>`), exactly as the launcher does. Mountless, shared
# skills off, an empty static MCP set, an explicit name, and the given v3 kit (an absolute path) or
# none. sbx's own record of the composition, `sbx inspect --json`, is saved beside the create
# output; gates/preflight.py's composition check reads it.
#
# Requires: ROOT (repository root), WORK, record(), run_sbx() from the sourcing script.

BUILDX_BUILDER=$(python3 -c 'import json; print(json.load(open("runtime/versions.yaml"))["kit_build"]["builder"])')
export BUILDX_BUILDER
# Only THIS run's composition records may be judged.
rm -f "$WORK"/inspect-*.json "$WORK"/inspect-*.err

pinned_base_ref() {
    # $1 = backend (claude|codex). Prints <repository>@<digest>, or nothing.
    python3 -c 'import json,sys; b=json.load(open("runtime/versions.yaml"))["sandbox_bases"][sys.argv[1]]; print(b["base"] + "@" + b["version"] if b.get("base") and b.get("version") else "")' "$1"
}

create_pinned() {
    # $1 = backend, $2 = sandbox name, $3 = kit directory relative to ROOT (optional),
    # further arguments go to `sbx create` unchanged (e.g. --skills readonly for a control).
    backend=$1
    name=$2
    kit=${3:-}
    shift 2
    [ $# -gt 0 ] && shift
    ref=$(pinned_base_ref "$backend")
    record "base_ref_${name}" "$ref"
    if [ -z "$ref" ]; then
        record "create_${name}_exit" "no-pin"
        return 1
    fi
    skills=off
    case " $* " in *" --skills "*) skills= ;; esac
    if [ -n "$kit" ]; then
        run_sbx create "$ref" --name "$name" ${skills:+--skills "$skills"} --static-mcp none \
            --kit "$ROOT/$kit" "$@" >"$WORK/create-$name.txt" 2>"$WORK/create-$name.err"
    else
        run_sbx create "$ref" --name "$name" ${skills:+--skills "$skills"} --static-mcp none \
            "$@" >"$WORK/create-$name.txt" 2>"$WORK/create-$name.err"
    fi
    status=$?
    record "create_${name}_exit" "$status"
    run_sbx inspect "$name" --json >"$WORK/inspect-$name.json" 2>"$WORK/inspect-$name.err"
    record "inspect_${name}_exit" "$?"
    # The two observations every gate record already reads, now derived from sbx's own record.
    record "image_${name}" "${ref%@*}"
    record "workspace_line_${name}" "$(python3 -c 'import json,sys
try:
    d = json.load(open(sys.argv[1]))
except Exception:
    d = None
print("none · no workspace bind mount" if isinstance(d, dict) and d.get("host_mounts") == [] else "")' "$WORK/inspect-$name.json")"
    return "$status"
}
