# Vendored schema sources

## `agent-schema-v1.136.0.json`

| Field | Value |
|---|---|
| Upstream repository | `https://github.com/docker/docker-agent` |
| Tag | `v1.136.0` |
| Tag commit | `09ef3908edda9512741e5de1a882beb5a9408206` (the commit reported by the pinned `docker agent version`) |
| Upstream path | `agent-schema.json` (repository root) |
| SHA-256 | `0fa21814a841cfbf5bc53ede63616e9d732b30e9a66ac36bdcea47cfc82ebe57` |
| Size | 139683 bytes |
| JSON Schema dialect | draft-07 (`http://json-schema.org/draft-07/schema#`) |

Obtained byte-for-byte with `git show v1.136.0:agent-schema.json` from a clone of the
upstream repository. Check it with:

```sh
sha256sum tests/contract/agent-schema-v1.136.0.json
```

### What this file is, and what it is not

- It is the tag's **root/latest schema**. It describes Docker Agent **config version 16**
  (`"description": "Configuration schema for Docker Agent v16"`; in v1.136.0,
  `pkg/config/latest` declares `Version = "16"`), while its `version` enum also accepts
  `"15"` and older values.
- It is **not** "the v15 schema". v1.136.0 parses a `version: "15"` file with its frozen,
  **strict** v15 parser (`pkg/config/v15`, unknown fields rejected) and then upgrades it to
  the latest in-memory config.
- In this repository it is only a **static sanity check** for the runtime configs
  (task T059).
- The **compatibility authority** for V1's `version: "15"` Docker Agent configs is the pinned
  v1.136.0 binary's strict v15 parser, exercised with `docker agent debug config`
  (tasks T055 and T061).
- V1 stays on config version **15** (`runtime/versions.yaml`:
  `docker_agent_config_version: 15`). Docker Agent isn't upgraded by vendoring this file.
