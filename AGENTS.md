# AGENTS.md — working in this repository

This file tells AI agents (Claude Code, Cursor, VS Code, LangChain MCP clients)
how to work safely with ApplyCanary and its CockroachDB memory layer.

## What ApplyCanary is

A job-search agent: it polls job boards, scores postings against a user's
resume, tailors CVs (with an anti-fabrication truthcheck), and runs **AI mock
interviews with real speech** (Amazon Polly/Transcribe, browser fallback).
CockroachDB is the **persistent memory layer**: transactional state (jobs,
applications, interview sessions), embeddings with a distributed vector index
(`job_embedding`, `agent_memory`), and long-term agent memory recalled
semantically before each interview.

## CockroachDB Agent Skills — load these first

The project uses the official [cockroachlabs/cockroachdb-skills](https://github.com/cockroachlabs/cockroachdb-skills)
repository — a curated, machine-executable skill set. Install it with:

```bash
npx skills add cockroachlabs/cockroachdb-skills --list   # preview
npx skills add cockroachlabs/cockroachdb-skills --skill <name> --yes
```

Skills most relevant to this codebase:

- `onboarding-and-migrations/*` — before touching schema: `sync_schema()` is
  additive-only; migrations live in `scripts/migrate_multiuser.py`.
- `query-and-schema-design/*` — before writing queries against
  `job_embedding`/`agent_memory`: vector columns are `VECTOR(1024)` and rely on
  the `vec_cosine_ops` index; brute-force fallback happens in Python on SQLite.
- `performance-and-scaling/*` — the semantic-search SQL in
  `app/memory/vectors.py` must use `vec_cosine_distance`, never a full scan.
- `security-and-governance/*` — every per-user query is scoped by `user_id`;
  audit logs (MCP + ccloud) record all agent access.
- `resilience-and-disaster-recovery/*` — run `scripts/ccloud/backup.sh` before
  destructive work; `data/applycanary.db` is never the source of truth in prod.

## Non-negotiables

1. **Never point tests at the real database.** `tests/conftest.py` redirects
   to a temp SQLite file; `DATABASE_URL`/`DATA_DIR` exported in a shell must
   not leak into pytest runs.
2. **Per-user scoping is load-bearing.** `current_user` → filter every
   `JobScore`/`Application`/`InterviewPrep`/`InterviewSession`/`AgentMemory`
   query by `user_id`; a missing row means NEW/untouched.
3. **Vector dims must match.** Embeddings are stored at 1024 (`EMBEDDING_DIMS`);
   changing the model requires a re-embed backfill (`POST /api/actions/embed-all`).
4. **Truthfulness gates stay.** Tailored CVs go through `truthcheck`; interview
   feedback is grounded in the user's actual resume and answers.
5. **Verify with the real tools:** `pytest`, `ruff check app/ tests/ scripts/`,
   and `cd frontend && npm run build`.

## Tool access to the cluster

- **MCP server** (read-only, audited): `mcp/cockroachdb-mcp.json`.
- **ccloud CLI** (provision/backup/audit): `scripts/ccloud/`.
- **SQL** for the app's own writes goes through the FastAPI API, never ad-hoc.
