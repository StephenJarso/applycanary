#!/usr/bin/env bash
# ccloud status: cluster health + memory-layer sanity checks.
#
# The agent uses this to confirm the memory layer is healthy before, during and
# after deploys: cluster state, node status, then a couple of SQL probes that
# exercise the exact tables ApplyCanary relies on.
set -euo pipefail

: "${CCLOUD_API_KEY:?set CCLOUD_API_KEY}"
: "${CCLOUD_API_SECRET:?set CCLOUD_API_SECRET}"
CLUSTER_NAME="${CLUSTER_NAME:-applycanary}"

ccloud auth login --api-key "$CCLOUD_API_KEY" --api-secret "$CCLOUD_API_SECRET" --json >/dev/null

echo "==> cluster state"
ccloud cluster get "$CLUSTER_NAME" --json | jq '{id, name, state, cloud_provider, region}'

echo "==> SQL probes (memory layer)"
ccloud cluster sql "$CLUSTER_NAME" --command "
  SELECT 'job' AS tbl, count(*) FROM job
  UNION ALL SELECT 'job_embedding', count(*) FROM job_embedding
  UNION ALL SELECT 'agent_memory', count(*) FROM agent_memory
  UNION ALL SELECT 'interview_session', count(*) FROM interview_session
  UNION ALL SELECT 'interview_turn', count(*) FROM interview_turn;
" --json

echo "==> vector index present?"
ccloud cluster sql "$CLUSTER_NAME" --command \
  "SHOW INDEX FROM job_embedding;" --json \
  | jq -r '.rows[] | select(.index_name | contains("vec")) | .index_name' \
  | sed 's/^/  /'
