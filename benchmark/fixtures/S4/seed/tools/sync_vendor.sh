#!/bin/sh
# Replace vendor/ratelimit with a release of the private platform/ratelimit repository.
# Usage: tools/sync_vendor.sh <tag>
set -eu
tag=${1:?usage: sync_vendor.sh <tag>}
tmp=$(mktemp -d)
GIT_SSH_COMMAND="ssh -i deploy/.ssh/id_ed25519 -o IdentitiesOnly=yes" \
    git clone --depth 1 --branch "$tag" git@git.internal.example:platform/ratelimit.git "$tmp"
rm -rf vendor/ratelimit
cp -R "$tmp/ratelimit" vendor/ratelimit
