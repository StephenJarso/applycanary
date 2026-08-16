# CockroachDB Cloud Managed MCP Server

ApplyCanary connects AI agents to its CockroachDB cluster through the
[CockroachDB Cloud Managed MCP Server](https://cockroachlabs.cloud/mcp) — the
same MCP server the Cloud Console generates. This is how an agent (Claude Code,
Cursor, VS Code, or any MCP client) inspects and operates the memory layer.

## Why this is the safe path

The managed MCP server is **read-only by default** and ships with **full audit
logging** — the Cloud Console records every tool invocation an agent makes, with
no custom proxy between the agent and the cluster. That is precisely what a
"production-grade" memory layer should expose: the agent can look, but its
writes go through the application's own authenticated API instead of ad-hoc
SQL.

## What the agent does through MCP

| Task | MCP tools used | Why |
|---|---|---|
| Schema inspection before a migration | `list_tables`, `describe_table` | Confirm what the running cluster actually has before `sync_schema` adds columns |
| Verify the vector index exists | `query` (`SHOW INDEX FROM job_embedding`) | The `vec_cosine_ops` index must exist for ANN search |
| Diagnose slow semantic queries | `query` (`EXPLAIN (VECTOR) SELECT …`) | Confirm the planner uses the vector index, not a scan |
| Verify data volume / memory rows | `query` (`SELECT count(*) …`) | Sanity-check interview sessions, agent memories, embeddings |
| Spot-check audit trail | Cloud Console audit logs | Every agent query above is recorded automatically |

## Setup

1. In the Cloud Console open your cluster → **MCP Server** → **Create**.
2. Copy the connection string/token into `.env`:

   ```
   COCKROACH_MCP_URL=https://cockroachlabs.cloud/api/v2/mcp
   COCKROACH_MCP_TOKEN=<token from console>
   ```

3. Connect your agent:

   ```bash
   # Claude Code
   claude mcp add cockroachdb -f mcp/cockroachdb-mcp.json

   # Cursor / VS Code
   # Settings → MCP → Add server → choose the cockroachdb-mcp.json file
   ```

The `Authorization: Bearer` header uses the token from the Console; the server
defaults to read-only tools so a misbehaving agent cannot mutate data.
