# ccloud CLI — the agent's control plane for the memory layer

ApplyCanary uses the [ccloud CLI](https://www.cockroachlabs.com/docs/cockroachcloud/install-ccloud)
(CockroachDB Cloud's agent-ready CLI) to provision, monitor, back up, and audit
the CockroachDB cluster that is its memory layer. ccloud is designed for AI:
consistent **noun-verb** commands, **JSON output on every call**, and
**service-account RBAC** so the agent gets exactly the permissions it needs and
nothing more.

## Why the agent uses ccloud instead of the console

- **JSON on every command** — the output is parseable, so the agent can take
  the connection string it just created, put it in `.env`, and verify state
  without screen-scraping.
- **Service-account credentials** — `ccloud auth login --api-key … --api-secret …`
  with scoped roles; no human password sitting in a CI secret.
- **Idempotent by construction** — every script below checks state before
  acting (`cluster list` before `cluster create`), so re-running a provision
  never creates a duplicate cluster.

## Scripts

| Script | What it does | When the agent runs it |
|---|---|---|
| `provision.sh` | Create the serverless cluster, database, enable backups, print the `DATABASE_URL` | First deploy / cluster re-creation |
| `status.sh` | Cluster state + row counts for `job`, `job_embedding`, `agent_memory`, `interview_session`, `interview_turn` + vector-index check | Pre/post deploy, incident triage |
| `backup.sh` | List scheduled backups, trigger an on-demand one | Before destructive migrations, monthly cadence |
| `audit.sh` | Tail audit-log events (including MCP invocations) | Security review, incident response |

## Usage

```bash
export CCLOUD_API_KEY=... CCLOUD_API_SECRET=...   # service account with Cluster Admin
./scripts/ccloud/provision.sh
./scripts/ccloud/status.sh
./scripts/ccloud/backup.sh
./scripts/ccloud/audit.sh
```

Every command prints JSON (`--json`); scripts pipe it through `jq` for the
human, but an agent can consume the raw output directly.

## RBAC (least privilege)

Create a dedicated service account for the app's operations:

```bash
ccloud iam service-account create applycanary-ops --json
ccloud iam role grant applycanary-ops --role 'Cluster Operator' --resource "$CLUSTER_NAME" --json
```

Keep a second, **read-only** service account for the MCP server so diagnostic
queries can never mutate anything.
