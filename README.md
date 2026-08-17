# ApplyCanary

**Your job search, remembered.** An agentic job-search assistant that finds,
scores, and tailors applications — then practises the interview with you out
loud, **remembering you across sessions** because its memory lives in
CockroachDB.

Built for the CockroachDB × AWS hackathon: CockroachDB is the agent's
persistent memory layer (transactions, **distributed vector indexing**, and
long-term recall), and AWS powers the models and the voice (Bedrock, Polly,
Transcribe, S3, ECS).

> **Hackathon submission details:** [docs/HACKATHON.md](docs/HACKATHON.md) ·
> demo video script: [docs/DEMO.md](docs/DEMO.md) · MIT licensed.

---

## What it does

**Finds jobs.** Ten connectors — company ATS boards (Greenhouse, Lever, Ashby,
SmartRecruiters, Workable) polled every 5 minutes for the apply-early edge, plus
five aggregators. Dedup collapses cross-posted roles into one row.

**Hunts *your* roles specifically.** Role discovery builds queries from your
target titles, skills and GitHub evidence and actively searches Adzuna + the
web for them (every 6 hours, plus a "Discover roles for me" button) — so a Go
developer actually sees Go roles, not just whatever the configured boards
happen to carry. Search is tokenized across title/company/description, so
"go developer" finds "Senior Golang Engineer" and surfaces matches even before
the scheduler has scored them.

**Scores against *your* resume.** Two tiers: free local filters (hard
knockouts, keyword overlap) then an LLM that reasons about fit using the actual
job description and your actual experience. Grounded, calibrated, and cached.

**Tailors CVs — truthfully.** Rewrites your resume against the posting with
your GitHub repos as evidence, then a separate **truthcheck** pass blocks any
claim that isn't backed by your real history. An unverified draft can never be
submitted.

**Practises the interview — out loud.** The AI Interview Studio is a live mock
interview for a real posting. A spoken coach asks the questions an interviewer
would, hears your answers (Amazon Transcribe, or browser speech), scores each
one against the posting's rubric, and gives specific coaching. No API keys? It
still works — browser speech synthesis/recognition take over automatically.

**Remembers you.** Every session is stored as transactional state *and* as an
embedded memory. The coach semantically recalls your past feedback before each
new question — "you rushed the last behavioural answer" — and the Memory page
shows your improvement trend. Memory isn't a feature here; it's the point.

**Emails you at your threshold.** Set an alert percentage on your profile
(default 90) — any posting scoring at/above it is emailed to *you* the moment
it's found (profile email → account email → your digest override), and a
per-user daily digest summarises applications, the review queue and new
matches. Per-user filtering means one user's activity never leaks into
another's mail.

---

## Architecture

```mermaid
flowchart LR
    subgraph Browser
        UI[React dashboard]
    end
    subgraph AWS[Amazon ECS Fargate]
        API[FastAPI agent]
        SCH[Scheduler]
    end
    UI -->|/api| API
    API -->|transcript| B[Amazon Transcribe]
    API -->|TTS| P[Amazon Polly]
    API -->|LLM + embeddings| BR[Amazon Bedrock]
    API -->|audio| S3[(Amazon S3)]
    API <-->|postgres wire| CR[(CockroachDB Serverless)]
    CR -->|VECTOR + vec_cosine_ops index| IDX[(job_embedding, agent_memory)]
    CR -->|transactions| TX[(jobs, sessions, turns, applications)]
    MCP[MCP Server read-only] -.inspect.-> CR
    CCLOUD[ccloud CLI] -.provision/backup/audit.-> CR
```

## The CockroachDB memory layer

CockroachDB is the database, the vector store, and the agent's long-term
memory — one system, no consistency gaps:

| Table | Role |
|---|---|
| `job`, `job_score`, `application`, … | Transactional state: the shared job pool and per-user pipelines |
| `interview_session`, `interview_turn` | Interview state machine — close the tab, resume mid-question |
| `job_embedding` | **Native `VECTOR(1024)` column** with a distributed `vec_cosine_ops` index — semantic "similar roles" and job search are SQL queries |
| `agent_memory` | Long-term memories (interview summaries, coaching feedback) with embeddings, recalled **semantically** before each session |

Semantic search runs in the database: `1 - vec_cosine_distance(embedding, :q::vector)`.
On SQLite (dev/tests) the same code falls back to Python distance, so the
feature set is identical and the test suite stays hermetic.

The cluster is operated agent-first:
- **ccloud CLI** (`scripts/ccloud/`) — provision, status, backups, audit logs;
  JSON on every command, service-account RBAC.
- **CockroachDB Cloud Managed MCP Server** (`mcp/`) — read-only, fully audited
  agent access to inspect schema and verify the vector index.
- **CockroachDB Agent Skills** (`AGENTS.md`) — the official
  `cockroachlabs/cockroachdb-skills` wired in so any agent working here
  operates the memory layer correctly.

## AWS services

| Service | Role |
|---|---|
| **Amazon Bedrock** | Claude inference (in the multi-provider LLM chain) + Titan embeddings for the vector index |
| **Amazon Polly** | Neural TTS — the spoken interviewer |
| **Amazon Transcribe** | Streaming STT — hears spoken answers |
| **Amazon S3** | Versioned, private interview audio |
| **Amazon ECS / Fargate** | The agent itself, behind an ALB (see `deploy/aws/`) |
| **CloudWatch** | Logs, metrics, ALB access |

Every AWS feature degrades gracefully: no credentials, and the app still runs
on Gemini/OpenRouter/Groq/Ollama, browser speech, local embedding, and local
disk. With credentials it lights up the full stack.

### Free, never-exhausted LLM option: local inference

The provider chain is resilient: **xAI Grok** (OpenAI-compatible, `grok-4.6`)
→ Gemini free tier → Groq free tier → OpenRouter `:free` models → local
**Ollama** → Anthropic → Bedrock. Each provider carries a circuit breaker (bad
keys/quota walls are cooled down for minutes instead of retried into a storm),
so a dead key can never stall the scheduler or lock the database. **Ollama is
the only option that genuinely never runs out** — it's local, no quota, no
rate limit:

```bash
ollama pull llama3.1:8b
OLLAMA_HOST=http://localhost:11434 .venv/bin/python run.py
```

With no LLM at all the app still works: keyword scoring, rule-based ATS
tailoring (with the same truthcheck gate) and heuristic interview coaching.

---

## Quick start

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
cp .env.example .env        # add an LLM key: XAI_API_KEY, GEMINI_API_KEY, ...

cd frontend && npm ci && npm run build && cd ..

.venv/bin/python run.py
```

Or with Docker (builds the frontend for you):

```bash
cp .env.example .env
docker compose up -d
```

Then open **http://127.0.0.1:8000** (React dashboard) or
**http://127.0.0.1:8000/docs** (API reference). Create the first admin account
(which also prints an invite code) with `scripts/bootstrap_admin.py`, then
register in the UI, upload your resume in **Profile**, and polling starts on
its own.

**Interview studio:** open any job → **🎙 AI Interview**. Voice works in
Chrome/Edge out of the box; with `AWS_*` set you get Polly's neural voice and
Transcribe-grade accuracy.

## Production: CockroachDB + AWS

1. **Provision the cluster** — the memory layer:
   ```bash
   export CCLOUD_API_KEY=... CCLOUD_API_SECRET=...
   ./scripts/ccloud/provision.sh        # cluster + DB + backups, prints DATABASE_URL
   ```
2. **Point the app at it**:
   ```bash
   DATABASE_URL='postgresql://user:password@host:26257/applycanary?sslmode=require' \
   SECRET_KEY=$(python3 -c 'import secrets; print(secrets.token_urlsafe(48))') \
   python run.py
   ```
   On boot, `init_db()` creates the schema — including the `VECTOR(1024)`
   columns and the `vec_cosine_ops` indexes — and `sync_schema()` reconciles
   missing columns idempotently.
3. **Deploy on AWS** (ECS Fargate + ALB + S3 + IAM for Bedrock/Polly/Transcribe):
   see [deploy/aws/README.md](deploy/aws/README.md). Secrets go in SSM, never
   in git or the image.
4. **Connect your agent to the cluster** (read-only, audited): see
   [mcp/README.md](mcp/README.md).

### Frontend hosting (Vercel)

The React dashboard is served from Vercel. The Vercel project's **Root Directory
must be `frontend`** — set it under Project Settings → General (it is a project
setting, *not* a `vercel.json` key). `frontend/vercel.json` rewrites `/api/*` and
`/health` to the API backend (Railway by default). To deploy manually, run
`vercel --prod` from the **repository root**; the Root Directory setting then
resolves `frontend/` correctly.

## Configuration

Everything is environment variables; see `.env.example` for the annotated
list. The ones that matter most:

| Variable | Default | Notes |
|---|---|---|
| `DATABASE_URL` | `sqlite:///./data/applycanary.db` | CockroachDB: `postgresql://…?sslmode=require` |
| `AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY` | — | Enables Bedrock + Polly + Transcribe + S3; IAM in production |
| `AWS_REGION` | `us-east-1` | Keep near the CockroachDB cluster |
| `BEDROCK_MODEL_ID` | Claude Sonnet 3.5 | Foundation model for scoring/tailoring/interview |
| `BEDROCK_EMBEDDING_MODEL_ID` | Titan v2 | Embeddings for the vector index (1024 dims) |
| `S3_BUCKET` | — | Interview audio storage |
| `COCKROACH_MCP_TOKEN` | — | MCP server token (agent access to the cluster) |
| `XAI_API_KEY` | — | xAI Grok — tried first (OpenAI-compatible; `grok-4.6`), needs credits on the team |
| `GEMINI_API_KEY` / `OPENROUTER_API_KEY` / `GROQ_API_KEY` | — | Alternative LLM providers (chain falls through) |
| `ENABLE_AUTO_SUBMIT` | `false` | Live application submission — off unless you mean it |
| `OLLAMA_HOST` | `http://localhost:11434` | Local, quota-free LLM — the "never runs out" option |
| `POLLY_VOICE_ID` | `Joanna` (female) | Interviewer voice; browser fallback also prefers female voices |
| `SECRET_KEY` | built-in (refused on public bind) | Session signing; random ≥32 bytes in production |

## Security

- Per-user scoping on every handler; scrypt password hashing; signed session
  tokens invalidated on password change; invite-gated registration.
- No auto-submission unless you turn it on, and the truthcheck gate blocks
  unverifiable CVs in both modes.
- Secrets in SSM, never in git or the image; private, versioned S3;
  least-privilege IAM; read-only audited MCP access.
- `/health` reports scheduler state so a hung agent fails the load-balancer
  check. Backups are enabled at provision time and on-demand scripted.

## Tests

```bash
.venv/bin/python -m pytest -q        # 215 tests: auth, dedup, ATS, truthcheck,
                                     # interview coach, vector search, memory,
                                     # search, discovery, email, LLM fallback
.venv/bin/python -m ruff check app/ tests/ scripts/
cd frontend && npm run build          # TypeScript + Vite
```

Tests always run on a throwaway SQLite database (`tests/conftest.py` redirects
it) — never on your real data.

## Repository layout

```
app/
  db.py            engine + VectorType + vector-index bootstrap (CockroachDB/SQLite)
  models.py        SQLModel schema incl. job_embedding, agent_memory, interview_*
  memory/          embeddings (Bedrock Titan → local), vector search, memory writes
  speech/          interview coach state machine, Polly TTS, Transcribe STT
  llm/             multi-provider chain incl. Amazon Bedrock + circuit breaker
  pipeline/discover.py  role-driven discovery (target titles + skills + GitHub)
  api/             REST API (jobs, profile, interview, memory, similar, search)
  scheduler.py     background jobs incl. embedding backfill
frontend/
  src/pages/       Jobs, JobDetail, InterviewStudio, Memory, …
mcp/               CockroachDB Cloud Managed MCP server config + docs
scripts/ccloud/    ccloud CLI: provision, status, backup, audit
deploy/aws/        Terraform: ECS Fargate + ALB + S3 + IAM
docs/              HACKATHON.md, DEMO.md, plus ops guides
AGENTS.md          CockroachDB Agent Skills wiring
```
