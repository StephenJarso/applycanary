# Architecture

> **Note:** this document describes the core application pipeline. For the
> current architecture — CockroachDB as the memory layer (vector indexing,
> interview sessions, agent memory) and the AWS deployment — see the
> [README](../README.md#architecture) and
> [deploy/aws/README.md](../deploy/aws/README.md).

## What this system does

Polls company job boards, deduplicates the results, scores each posting against
the user's resume, tailors a CV where the fit is good, and either queues the
application for one-click review or submits it automatically. It runs
unattended on a schedule so a posting can be acted on within minutes of going
live.

## Design principles

Four constraints shaped nearly every decision below.

**Cost scales with postings, not with matches.** A polling loop across dozens of
boards sees thousands of postings per day. Sending each one to a language model
would cost more than the tool saves, so expensive work is pushed behind cheap
local filters.

**Determinism where the user acts on the output.** ATS compatibility findings
tell the user to edit a real document. The same resume must always produce the
same verdict, so that engine is pure rules with no model involvement.

**Fabrication is a code-level concern, not a prompt-level one.** A prompt that
says "do not invent metrics" is a request. The tailoring output is verified
against source material in code, and failing that check disqualifies a CV from
automated submission.

**Automation defaults to off.** The irreversible action in this system is
sending an application to an employer. Every path to that action passes through
gates that are closed unless explicitly opened.

## Data flow

```
                      companies.yaml
                            |
                            v
   +--------------------------------------------------+
   |  sources/     Greenhouse  Lever  Ashby           |
   |               SmartRecruiters  RemoteOK          |
   +--------------------------------------------------+
                            |  raw postings
                            v
   +--------------------------------------------------+
   |  pipeline/normalize.py   canonical company,      |
   |                          title, location         |
   +--------------------------------------------------+
                            |
                            v
   +--------------------------------------------------+
   |  pipeline/dedup.py    (1) fingerprint            |
   |                       (2) canonical URL          |
   |                       (3) fuzzy title in company |
   +--------------------------------------------------+
                            |  unique postings -> Job
                            v
   +--------------------------------------------------+
   |  pipeline/score.py                               |
   |    tier 1  hard filters + keyword coverage  FREE |
   |             |                                    |
   |             v  survivors only                    |
   |    tier 2  LLM rubric, cached resume prefix      |
   +--------------------------------------------------+
                            |  Score
                            v
   +--------------------------------------------------+
   |  pipeline/tailor.py                              |
   |    evidence from pipeline/github.py              |
   |    rewrite -> pipeline/truthcheck.py  GATE       |
   +--------------------------------------------------+
                            |  tailored CV + cover letter
                            v
   +--------------------------------------------------+
   |  apply/base.py   SubmitGate                      |
   |    dry-run? truthcheck passed? score >= min?     |
   |    daily cap? not already applied?               |
   +----------+---------------------------+-----------+
              |                           |
              v                           v
     apply/smartrecruiters.py      apply/manual.py
     (API submission)              (review queue)
              |                           |
              +-------------+-------------+
                            v
                       Application
                            |
                            v
                   notify/email.py digest
```

## Layers

### Sources (`app/sources/`)

Each connector subclasses `BaseSource` and implements a single `fetch` method.
Retries, timeouts, HTML flattening, and error isolation live in the base class so
a connector stays a thin parser over one API shape. Connectors self-register via
a `@register` decorator; `all_sources()` returns the registry.

Adding a board means writing one file and importing it in
`app/sources/__init__.py`. Nothing else changes.

Failures are isolated per source and recorded in `SourceRun`. A board that
starts returning empty results is a silent failure that looks identical to "no
new jobs today", so every run persists its posting count and error state, and
the dashboard surfaces connectors that have gone quiet.

### Deduplication (`app/pipeline/dedup.py`)

The same job appears on a company's own board and on aggregators, often with
different titles and tracking parameters. Three layers run in order, cheapest
first:

1. **Fingerprint** — hash of normalised company, title, and location. Catches
   verbatim reposts.
2. **Canonical URL** — strips tracking parameters and fragments. Catches the
   same posting reached through different referrers.
3. **Fuzzy title within a company** — `rapidfuzz` similarity above a threshold.
   Catches "Software Engineer II" against "Software Engineer 2".

Layer 3 is the risky one: collapsing genuinely distinct requisitions hides real
openings. Level and scope tokens (`senior`, `staff`, `manager`, `intern`) block
a fuzzy match even at high similarity, so "Engineer" never merges with
"Engineering Manager".

Duplicates are recorded as `JobAlias` rows rather than discarded, which keeps
dedup auditable — you can see every place a job was seen and why two postings
were considered the same.

### Scoring (`app/pipeline/score.py`)

Two tiers, because scoring every posting with a model does not pay for itself.

**Tier 1** runs locally and free: hard filters (location, work authorisation,
seniority, excluded companies) and keyword coverage from
`pipeline/keywords.py`. Most postings are eliminated here.

**Tier 2** sends survivors to the model with a rubric covering skill match,
seniority fit, domain relevance, and growth signal. The resume is identical
across every posting in a cycle, so it sits in a cached prompt prefix — the
single largest cost lever in the system. Static prompt blocks are module
constants in `app/llm/prompts.py` because they must stay byte-identical for the
cache to hit.

### ATS compatibility (`app/pipeline/ats_rules.py`)

Pure rules, no model. Real ATS parsers fail on document structure more often
than on wording, so `app/resume/parse.py` extracts layout signals — multi-column
layouts, tables, text in headers and footers, images, unparseable sections — and
the rule engine scores them into a 0–100 report with specific findings.

`app/resume/render.py` generates single-column DOCX and PDF with no tables and
no header text, because each of those is something the rule engine flags.
Generating output that fails your own checker would be self-defeating.

### Evidence and verification (`app/pipeline/`)

`github.py` collects verifiable evidence from public repositories: languages,
frameworks, project descriptions, commit activity. Without evidence, asking a
model to "add the missing keywords" is an invitation to fabricate.

`truthcheck.py` verifies every tailored claim against the original resume plus
that GitHub evidence. It blocks invented metrics (a percentage or dollar figure
absent from the source), invented credentials (degrees, certifications,
clearances), and unsupported skills. It **fails closed**: if a checker raises,
the result is a blocking violation, never a silent pass. `tests/test_truthcheck.py`
is the sharpest test file in the suite, because a false pass here puts a
fabricated claim in front of an employer.

### Apply layer (`app/apply/`)

`SubmitGate` in `base.py` centralises every precondition: dry-run mode,
truthcheck status, minimum score, daily cap, and duplicate-application check.
The truthcheck condition is evaluated **above** the `force` branch, so a user
override cannot bypass it. Centralising this means a new submitter backend
cannot accidentally skip a gate.

`smartrecruiters.py` submits through the one platform in this source set with a
public candidate API. `manual.py` handles everything else: it prepares the
tailored CV, cover letter, and pre-filled form answers, then queues the job for
review. That preserves most of the speed advantage without automating a
submission the platform has not sanctioned.

Resume file attachment is deliberately unimplemented in the SmartRecruiters
submitter — the multipart contract could not be verified against the live API,
and a guessed shape fails in the worst way available: looking like a successful
submission while arriving without a resume.

### Scheduling and delivery

`app/scheduler.py` runs APScheduler jobs for polling, scoring, preparation, and
the daily digest. Every job is wrapped in an error guard, because an unhandled
exception silently cancels an APScheduler job's future runs — indistinguishable
from "the bot stopped finding jobs".

`app/web/` serves the dashboard with FastAPI and Jinja templates.
`app/notify/email.py` sends the digest and immediate high-score alerts, falling
back to logging when SMTP is unconfigured so a missing password never loses
information.

## Storage

SQLite in WAL mode. The scheduler writes while the dashboard reads; WAL plus a
busy timeout avoids `database is locked` under normal single-user operation.

Eight tables, defined in `app/models.py`: `profile`, `job`, `job_alias`,
`job_score`, `resume_version`, `application`, `interview_prep`, and
`source_run`. See [DATA_MODEL.md](DATA_MODEL.md) for the field-level reference.

## Security posture

**The dashboard has no authentication.** It exposes your resume, scores, and
application history. `app/config.py` warns at startup if `HOST` is not
loopback. Do not expose it to a network without putting authentication in front
of it.

Secrets live in `.env`, which is gitignored: Anthropic API key, SMTP password,
GitHub token. `data/` is also gitignored — it holds the database, uploaded
resumes, and generated CVs.

The GitHub scan reads public repository data only.

## Known limitations

- Dependency versions in `requirements.txt` are floors, not pins, and were not
  resolved against a live index.
- The test suite has not been executed in the authoring environment.
- Source connectors were written against documented API shapes and tested
  against recorded payloads, not verified live. Run
  `scripts/verify_sources.py` before relying on any of them.
- Only SmartRecruiters supports automated submission. Everything else routes to
  the manual queue.
- Salary parsing is best-effort and is never used to reject a posting.

