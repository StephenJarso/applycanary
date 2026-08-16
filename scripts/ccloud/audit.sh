#!/usr/bin/env bash
# ccloud audit: surface recent audit-log events for the cluster.
#
# Who did what, when — the Cloud Console logs every SQL statement and every
# API call (including MCP server invocations). Run this after any incident or
# before a security review to see the agent's own footprint.
set -euo pipefail

: "${CCLOUD_API_KEY:?set CCLOUD_API_KEY}"
: "${CCLOUD_API_SECRET:?set CCLOUD_API_SECRET}"
CLUSTER_NAME="${CLUSTER_NAME:-applycanary}"
LIMIT="${LIMIT:-25}"

ccloud auth login --api-key "$CCLOUD_API_KEY" --api-secret "$CCLOUD_API_SECRET" --json >/dev/null

echo "==> recent audit events for '$CLUSTER_NAME' (last $LIMIT)"
ccloud audit log list \
  --cluster "$CLUSTER_NAME" \
  --limit "$LIMIT" \
  --json \
  | jq -r '.[] | [.timestamp, .actor_name // .actor, .event_type, .resource_name // ""] | @tsv'
