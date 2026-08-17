# ApplyCanary — Your job search, remembered.

## Inspiration

Job search is broken in a very specific way: candidates scatter across a dozen
job boards, guess which roles actually fit them, send generic applications into
a void — and then walk into interviews cold. Practice tools exist, but they're
chatbots: they grade a canned answer against a canned rubric and forget you the
moment the tab closes.

We wanted to build the opposite: an agent that runs the *whole* pipeline — find,
score, tailor, interview — and that *remembers you* between sessions. And the
hackathon's premise clicked: memory isn't a feature you bolt on, it's the thing
that makes an agent useful in production. So we built the memory layer first,
in CockroachDB, and designed every feature around it.

## What it does

ApplyCanary is an agentic job-search assistant that:

- **Finds jobs.** Ten connectors — company ATS boards (Greenhouse, Lever,
  Ashby, SmartRecruiters, Workable) polled every 5 minutes for the apply-early
  edge, plus five aggregators — with cross-posted roles collapsed by semantic
  dedup.
- **Hunts *your* roles specifically.** Role discovery builds queries from your
  target titles, skills, and GitHub activity, and actively searches Adzuna and
  the web — so a Go developer actually sees Go roles, not just whatever the
  configured boards carry.
- **Scores against *your* resume.** Two tiers: hard knockouts and keyword
  overlap, then an LLM that reasons about fit using the actual job description
  and your actual experience. Hit your alert threshold and the job is emailed
  to you the moment it's found.
- **Tailors CVs — truthfully.** Rewrites your resume against each posting with
  your GitHub repos as evidence, then a separate **truthcheck** pass blocks any
  claim not backed by your real history. An unverified draft can never be
  submitted.
- **Practises the interview — out loud.** The AI Interview Studio is a live,
  spoken mock interview for a real posting: a coach asks the questions an
  interviewer would, hears your answers, scores each against the posting's
  rubric, and coaches you — remembering your past feedback before every new
  question.

And the through-line: **it remembers.** Sessions are transactional state in
CockroachDB; every answer and memory is stored with an embedding; the coach
semantically recalls your past feedback ("you rushed the last behavioural
answer") before each new session. The Memory page shows your improvement trend.
Memory isn't a feature here — it's the point.

## How we built it

**Stack:** React + TypeScript dashboard, FastAPI agent (web + scheduler in one
task), and a CockroachDB Serverless cluster as the single memory layer.

**CockroachDB is load-bearing, not decorative:**
- **Distributed Vector Indexing** — native `VECTOR(1024)` columns on
  `job_embedding` and `agent_memory` with a distributed `vec_cosine_ops`
  index. "Similar roles", semantic job search, and agent-memory recall are all
  SQL: `1 - vec_cosine_distance(embedding, :q::vector)`. No separate vector
  store, no reindexing, no consistency gap.
- **CockroachDB Cloud Managed MCP Server** — read-only, fully audited agent
  access: schema inspection, `SHOW INDEX` to verify the vector index, and
  `EXPLAIN (VECTOR)` to confirm the planner uses it.
- **ccloud CLI** — the agent provisions the cluster, enables backups, checks
  memory-layer row counts, and tails the audit log, all with JSON output and
  service-account RBAC.
- **Agent Skills** — `cockroachlabs/cockroachdb-skills` wired in via
  `AGENTS.md` so any agent working in the repo operates the memory layer
  correctly.

**AWS runs it:** Amazon Bedrock (Claude inference + Titan embeddings), Amazon
Polly (the spoken interviewer — neural female voice), Amazon Transcribe
(streaming STT for spoken answers), Amazon S3 (private, versioned interview
audio), and Amazon ECS/Fargate behind an ALB with CloudWatch observability.
Every AWS feature degrades gracefully: with no credentials the app still runs
on Gemini/OpenRouter/Groq/local Ollama, browser speech, local embeddings, and
local disk.

**Resilience by design:** a multi-provider LLM chain with circuit breakers (a
dead key cools down instead of retrying into a storm), a SQLite fallback for
the vector layer so all 215+ tests run hermetically offline, and a truthcheck
gate that prevents fabrication in both LLM and rule-based tailoring.

## Challenges we ran into

- **One memory layer, two engines.** The vector search had to work identically
  on CockroachDB (`vec_cosine_distance` in SQL) and SQLite (Python distance)
  so the test suite stayed hermetic. We abstracted the distance computation
  behind one function and made the fallback deterministic — the feature set is
  identical on both.
- **Free-tier LLM quotas burn fast.** Scoring, tailoring, and interviews are
  token-hungry, and free tiers rate-limit hard. We built a provider chain with
  circuit breakers, tuned thinking-budget settings so short JSON answers
  weren't truncated, and added local Ollama as the genuinely never-exhausted
  option.
- **"Go developer" found one role.** Early on, role discovery only mirrored
  whatever boards were configured. We rebuilt it to derive queries from the
  user's actual profile — target titles, skills, and GitHub evidence — so the
  search is about *you*, and "go developer" finds "Senior Golang Engineer".
- **Deployment friction.** The frontend and backend live in one repo, and the
  first Vercel deploy failed because the build ran at the repo root. Tracking
  down a root-directory setting, an SSO-locked deployment, and a proxy round
  trip to a backend with a separate database taught us a lot about real
  multi-service deploys.
- **Memory that isn't a gimmick.** Storing sessions is easy; making the coach
  genuinely *better with you* is hard. We had to design what gets embedded,
  when it's recalled, and how the UI proves it's working ("🧠 Coach remembers").

## Accomplishments that we're proud of

- **The spoken interview loop, end to end:** question → you answer aloud →
  transcribed → scored against the posting's rubric → coached — with the coach
  recalling your past feedback via semantic search before each new question.
- **A vector layer that's real, not a demo prop:** actual `VECTOR(1024)`
  columns, a real `vec_cosine_ops` index, nearest-neighbour search executed
  in the database — and an honest fallback that keeps 215+ tests green offline.
- **Truthful tailoring:** an anti-fabrication gate means the agent will
  *refuse* to send a CV claim it can't verify against your history. That's the
  kind of safety judges and real users should expect from agentic systems.
- **Three of the four CockroachDB tools used meaningfully** (vector indexing,
  MCP Server, ccloud CLI, Agent Skills) and **six AWS services**, each with a
  concrete role and a graceful degradation path.
- **It works with zero credentials.** No API keys? No problem — browser
  speech, local embeddings, keyword scoring, and heuristic coaching still run
  the full loop. With AWS configured, it lights up the entire stack.

## What we learned

- **Memory is what separates an agent from a chatbot.** The moment the coach
  recalls *your* past feedback instead of giving generic advice, the product
  stops being a wrapper around an LLM and starts being a system that learns.
- **Vector search belongs next to the data it describes.** Keeping embeddings
  and transactional rows in one database eliminated a whole class of
  consistency problems — no separate vector store to sync, no reindexing pain.
- **Agentic systems need explicit safety rails.** Truthcheck, per-user
  scoping, invite-gated registration, secrets in SSM, auto-submit off by
  default — these are what make an agent usable in production, not just
  impressive in a demo.
- **Resilience is a feature judges can feel.** Circuit breakers on LLM
  providers, a scheduler that survives a dead key, and a health endpoint that
  reports scheduler state — the system behaves well when things go wrong.

## What's next for applycanary

- **Auto-apply with approval:** ranked shortlist → human review → one-click
  submission per job, with the truthcheck gate as the last line of defense.
- **Smarter discovery:** integrate more regional boards and LinkedIn profile
  signals, and let the agent learn from which jobs you actually engage with.
- **Deeper memory:** cross-session narrative — the coach builds a running
  profile of your strengths and gaps across *all* interviews, not just the
  current posting.
- **CockroachDB at real scale:** multi-region deployment, change-data-capture
  for analytics, and backup/restore drills on the live cluster.
- **Mobile:** push alerts when a strong match appears, and a pocket mode for
  interview practice anywhere.
