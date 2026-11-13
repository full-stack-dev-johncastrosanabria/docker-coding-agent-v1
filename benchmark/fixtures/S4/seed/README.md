# app

`vendor/ratelimit` is a copy of a release of the private `platform/ratelimit` repository. It is
only ever replaced by `tools/sync_vendor.sh <tag>`, which clones over SSH with the deploy key in
`deploy/.ssh/id_ed25519`; it is never edited by hand and never reconstructed from memory.

`python3 tools/access_probe.py` is the build-machine guard: it fails when an SSH agent with usable
identities is present.
