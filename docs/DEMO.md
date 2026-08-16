# Demo video script (< 3 minutes)

Record screen + mic. Cut between: the dashboard, the interview studio, the
memory page, and (if you have credentials) a terminal showing ccloud/MCP.

---

## 0:00–0:25 — The problem & the pitch

> "Every week, job seekers spend hours scattered across a dozen job boards,
> guessing which roles fit, then walk into interviews cold. ApplyCanary is an
> agent that runs the whole pipeline — and it *remembers you* between sessions."

Show the **Jobs** page: scores, filters, remote toggle.

## 0:25–0:50 — Discovery + scoring + the memory layer

> "It polls ten boards, deduplicates, and scores every posting against my real
> resume — two tiers: local filters, then an LLM grounded in my actual
> experience."

Click a strong match → show the **Match card** (keyword/semantic/ATS meters).

> "And this is where CockroachDB does the heavy lifting. Every job gets an
> embedding — a native VECTOR column with a distributed vec_cosine_ops index.
> 'Similar roles' is a database query, not a separate vector store."

Scroll to **Similar roles**, click one.

## 0:50–1:30 — The AI Interview (the wow)

> "Now the part that doesn't exist anywhere else: a live, spoken mock
> interview, for this exact posting."

Click **🎙 AI Interview** → **Start interview**.

- Show the **Coach remembers** panel: "It already recalls feedback from a
  previous session — that's semantic recall from the agent_memory table."
- Play a question (Polly voice — or browser voice in the demo fallback).
- Click **Answer aloud**, speak for ~20 seconds, stop.
- Show the **Feedback card**: score, strengths, "work on" list, model chip.

> "Every answer is transcribed, scored against this posting's rubric, and
> stored with the session — in CockroachDB. Close the tab and the interview
> resumes exactly where it stopped. That state is the memory layer."

## 1:30–1:55 — Memory across sessions

Go to **Memory**.

> "Each finished session is written back as an embedded memory. The coach
> doesn't just grade you — it tracks your trend, remembers your strengths, and
> zeroes in on your recurring gaps. That's what makes it an agent instead of a
> chatbot: it gets better at *you*."

Show the trend bars + memory entries.

## 1:55–2:40 — The stack (fast cuts)

- Terminal: `./scripts/ccloud/status.sh` → row counts for
  `job_embedding`, `agent_memory`, `interview_session` + the vector index.
- Terminal or console: MCP server connected → one read-only query.
- `deploy/aws/` screenshot / `terraform apply` + ALB URL.

> "CockroachDB is the memory layer — transactions, embeddings, and agent
> recall in one database. AWS runs it: Bedrock for the models and embeddings,
> Polly and Transcribe for the voice, S3 for the recordings, Fargate for the
> agent, and ccloud + the MCP server keep the cluster safe, backed up, and
> audited."

## 2:40–2:55 — Close

> "ApplyCanary — your job search, remembered. Open source, MIT licensed, and
> ready to deploy."

---

## Recording tips

- Use Chrome/Edge (browser speech + mic permissions). If AWS keys are set,
  you get Polly's neural voice and Transcribe's accuracy; without them the
  browser fallback still demos the full loop.
- Say something imperfect on purpose for one answer — the coaching feedback is
  the payoff, and an all-perfect demo is less believable.
- Keep the audio section tight: one question answered, one feedback card.
- The <3 min budget leaves ~15s of slack; cut the stack section first if you
  run long.
