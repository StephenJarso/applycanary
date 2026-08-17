# ApplyCanary — Submission Notes

Everything needed to answer the hackathon submission form, in one place:
the tools used, what the agent actually did with them, and how the pieces fit
together meaningfully.

**One-paragraph pitch:** ApplyCanary is an agentic job-search assistant that
finds postings on ten job boards, scores them against your real resume, tailors
ATS-safe CVs (with an anti-fabrication truthcheck gate), and then practises the
interview with you **out loud** — a spoken coach asks the questions a real
interviewer would, hears your answers, scores them against the posting's
rubric, and **remembers you across sessions** because its memory lives in
CockroachDB.

## Links

- **Repo:** <TODO — public GitHub URL, MIT licensed>
- **Demo app:** <TODO — deployed URL>
- **Video:** <TODO — YouTube/Vimeo, < 3 min>
- **Architecture diagram:** [`docs/assets/architecture.png`](assets/architecture.png)

---

## CockroachDB tools used (4 of the required 2)

### 1. Distributed Vector Indexing — the core retrieval engine
- Native `VECTOR(1024)` columns on `job_embedding` and `agent_memory`, backed
  by a distributed `vec_cosine_ops` index created at boot
  (`app/db.py` → `_ensure_vector_indexes`).
- Nearest-neighbour search is **SQL executed in the database**:
  `1 - vec_cosine_distance(embedding, :q::vector)` (`app/memory/vectors.py`).
  No separate vector store, no reindexing, no consistency gap.
- Powers **similar roles** on every job page, **semantic job search**, and
  **agent-memory recall** (the coach retrieves your past feedback before
  evaluating a new answer).
- Embeddings come from Amazon Bedrock Titan when AWS is configured, with a
  deterministic local embedder so the app runs zero-config (CI, offline, demo).
- The scheduler runs `backfill_embeddings()` so the index fills naturally as
  jobs arrive from the ten source connectors.

### 2. CockroachDB Cloud Managed MCP Server — safe agent access to the cluster
- `mcp/cockroachdb-mcp.json` + `mcp/README.md`: the single-config snippet from
  the Cloud Console, wired into Claude Code / Cursor / VS Code.
- Used read-only, with full audit logging: schema inspection before
  `sync_schema` runs, `SHOW INDEX` to verify the vector index, and
  `EXPLAIN (VECTOR)` to confirm the planner uses it.

### 3. ccloud CLI — the agent's control plane
- `scripts/ccloud/`: provision, status, backup, audit — JSON output on every
  command, service-account RBAC.
- The agent provisions the serverless cluster, enables nightly backups,
  sanity-checks memory-layer row counts, verifies the vector index, and tails
  the audit log.

### 4. CockroachDB Agent Skills (open-source repo)
- `AGENTS.md` wires `cockroachlabs/cockroachdb-skills` into any agent working
  in the repo: onboarding/migrations, query & schema design, vector-index
  performance, security & governance, resilience.

---

## AWS services used (6 of the required 1)

| Service | Role in the agent |
|---|---|
| **Amazon Bedrock** | Claude foundation-model inference via the `converse` API (first-class member of the multi-provider LLM chain) **+ Titan embeddings** that feed CockroachDB's `VECTOR(1024)` columns |
| **Amazon Polly** | Neural TTS — the spoken interviewer (female voice, `POLLY_VOICE_ID=Joanna`) |
| **Amazon Transcribe** | Streaming STT (16 kHz PCM) — hears the candidate's spoken answers |
| **Amazon S3** | Interview audio recordings (private, versioned) |
| **Amazon ECS / Fargate** | The containerized agent (web + scheduler in one task) behind an ALB, with least-privilege IAM and secrets in SSM (`deploy/aws/` Terraform) |
| **CloudWatch** | Logs, metrics, ALB access logs |

Every AWS capability degrades gracefully: with no credentials the app still
runs the full loop on Gemini/OpenRouter/local Ollama, browser speech, local
embeddings, and local disk; with AWS configured it lights up the entire stack.

---

## How the components are meaningfully integrated

The key insight: **AWS produces the intelligence (embeddings, reasoning,
voice); CockroachDB stores, indexes, and recalls it.** They meet inside the
agent's memory layer on every loop.

### The core integration: embeddings are AWS-made, CockroachDB-stored

1. **Titan embeddings are written into CockroachDB.** `app/memory/vectors.py`
   embeds every job (title + company + location + description) and every piece
   of agent memory with Bedrock Titan (1024 dims), then stores the vector in a
   native `VECTOR(1024)` column (`app/db.py` → `VectorType`).
2. **CockroachDB owns the retrieval.** Nearest neighbours are a **SQL query
   executed in the database** — `1 - vec_cosine_distance(e.embedding,
   :q::vector)` — backed by the distributed `vec_cosine_ops` index. AWS
   produces the numbers; CockroachDB indexes, scales, and answers.
3. **The scheduler keeps the index alive.** `backfill_embeddings()` runs from
   the ECS scheduler, so jobs arriving from the ten connectors are embedded and
   indexed automatically — no separate vector store to sync, no consistency
   gap between a row and its vector.

That single fused path powers three user-facing features: **"Similar roles"**
on every job page, **semantic job search**, and **agent-memory recall**
(`recall_memory`, per-user scoped) — the coach pulls your most relevant past
feedback before evaluating a new answer.

### The interview loop crosses both stacks on every turn

The most "agentic" integration in the product — each spoken answer round-trips
through AWS *and* CockroachDB:

1. **Polly** speaks the question (neural female voice) — the interviewer.
2. **Transcribe** hears the candidate's spoken answer via 16 kHz streaming STT.
3. The transcript is scored by the **Bedrock/Claude** model in the LLM chain
   against the posting's rubric.
4. The score, turn, and feedback are committed as **transactional state in
   CockroachDB** (`interview_session` / `interview_turn`) — close the tab, the
   interview resumes mid-question.
5. When a session finishes, `save_memory()` writes an **embedded memory** back
   to CockroachDB, and the next session **semantically recalls** it ("you
   rushed the last behavioural answer"). Memory as a product, not a prompt.

### The agent runs on AWS, operates CockroachDB safely

- **ECS/Fargate** hosts the FastAPI agent + scheduler behind an ALB; the ALB
  health check hits `/health`, which reports scheduler state — a hung agent
  fails the load-balancer check. Secrets live in SSM; IAM is least-privilege.
- **S3** stores private, versioned interview audio; **CloudWatch** collects
  logs, metrics, and ALB access.
- The **MCP Server** (read-only, audited) inspects the cluster before
  `sync_schema` runs, verifies the vector index with `SHOW INDEX`, and confirms
  the planner uses it with `EXPLAIN (VECTOR)`.
- The **ccloud CLI** provisions the serverless cluster, enables nightly
  backups, checks memory-layer row counts, and tails the audit log — all JSON,
  service-account RBAC.

### Production thinking: degrade gracefully

Every AWS capability has a no-credentials fallback (browser speech for
Polly/Transcribe, Gemini/OpenRouter/local Ollama for Bedrock, local embedder
for Titan), and vector search falls back to Python distance on SQLite — so the
215+ tests run hermetically while production uses the real distributed index.
The integration is deep where it matters and honest about its dependencies.
