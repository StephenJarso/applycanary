#!/usr/bin/env bash
# Provision the ApplyCanary CockroachDB Serverless cluster with ccloud.
#
# ccloud is the agent-ready CockroachDB Cloud CLI: consistent noun-verb
# commands, JSON output on every call, and service-account-based RBAC. The
# agent (or a human) drives cluster lifecycle entirely from the terminal.
#
# Usage:
#   export CCLOUD_API_KEY=... CCLOUD_API_SECRET=...     # service account
#   ./scripts/ccloud/provision.sh                        # create + configure
#
# Requires: ccloud CLI (https://www.cockroachlabs.com/docs/cockroachcloud/install-ccloud)
set -euo pipefail

: "${CCLOUD_API_KEY:?set CCLOUD_API_KEY (service account)}"
: "${CCLOUD_API_SECRET:?set CCLOUD_API_SECRET (service account)}"
CLUSTER_NAME="${CLUSTER_NAME:-applycanary}"
REGION="${REGION:-aws-us-east-1}"
DB_NAME="${DB_NAME:-applycanary}"

echo "==> logging into CockroachDB Cloud (service account)"
ccloud auth login --api-key "$CCLOUD_API_KEY" --api-secret "$CCLOUD_API_SECRET" --json

if ccloud cluster list --json | grep -q "\"name\": \"$CLUSTER_NAME\""; then
  echo "==> cluster '$CLUSTER_NAME' already exists; skipping create"
else
  echo "==> creating serverless cluster '$CLUSTER_NAME' in $REGION"
  ccloud cluster create serverless \
    --name "$CLUSTER_NAME" \
    --region "$REGION" \
    --cloud aws \
    --json
  # Wait for the cluster to be ready before touching it further.
  echo "==> waiting for cluster to become ready"
  until [ "$(ccloud cluster get "$CLUSTER_NAME" --json | jq -r .state)" = "READY" ]; do
    sleep 5
  done
fi

echo "==> creating database '$DB_NAME'"
# The SQL statement runs through the cluster's SQL endpoint — the same channel
# the app uses, so grants apply identically to app connections.
ccloud cluster sql "$CLUSTER_NAME" --command "CREATE DATABASE IF NOT EXISTS $DB_NAME;" --json

echo "==> enabling nightly backups + a retention window"
ccloud cluster update "$CLUSTER_NAME" --json \
  --set 'backups.enabled=true, backups.retention_days=14'

echo "==> cluster connection string (DATABASE_URL for .env)"
ccloud cluster connection-string "$CLUSTER_NAME" --database "$DB_NAME" --json

echo ""
echo "Done. Put the connection string in .env as DATABASE_URL and deploy."
echo "Ops scripts: ./scripts/ccloud/status.sh | backup.sh | audit.sh"
