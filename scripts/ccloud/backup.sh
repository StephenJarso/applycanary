#!/usr/bin/env bash
# ccloud backup: verify scheduled backups and take an on-demand one.
#
# The memory layer holds real user data (resumes, application history, agent
# memories) — backups are not optional. This is what the agent runs before a
# destructive migration or a risky deploy.
set -euo pipefail

: "${CCLOUD_API_KEY:?set CCLOUD_API_KEY}"
: "${CCLOUD_API_SECRET:?set CCLOUD_API_SECRET}"
CLUSTER_NAME="${CLUSTER_NAME:-applycanary}"

ccloud auth login --api-key "$CCLOUD_API_KEY" --api-secret "$CCLOUD_API_SECRET" --json >/dev/null

echo "==> scheduled backups"
ccloud cluster backups list "$CLUSTER_NAME" --json | jq -r '.[] | [.id, .state, .start_time] | @tsv'

echo "==> triggering an on-demand backup"
ccloud cluster backups create "$CLUSTER_NAME" --json | jq '{id, state}'
