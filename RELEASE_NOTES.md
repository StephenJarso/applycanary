# v0.1.0-rc.2

Second release candidate. A self-hosted job search agent: it polls ATS job
boards, scores postings against your resume, tailors a CV per posting behind a
verification gate, and queues applications for your approval.

Four bugs surfaced by the first real test run are fixed here. The suite, the
dashboard and a live ingest have now all been exercised — see
[What has been verified](#what-has-been-verified).

**This is a release candidate, not a release.** See [Known
limitations](#known-limitations) before deploying it.

## Read this first

Two defaults you need to know about, because both are deliberate and both
matter:

- **The dashboard has no authentication.** It serves your resume, your salary
  expectations and your full application history to anyone who can reach the
  port. It binds `127.0.0.1` by default, and Docker publishes it as
  `127.0.0.1:8000:8000`. Do not put it on a public interface without a reverse
  proxy and auth in front.
- **Nothing is submitted without your approval.** `ENABLE_AUTO_SUBMIT` defaults
  to `false`. Until you flip it, every application stops in the review queue as
  a dry run.

## What it does

**Job sources.** Connectors for Greenhouse, Lever, Ashby, SmartRecruiters and
RemoteOK behind a shared base class and registry, so adding a board is a parser
rather than new plumbing. Ingestion runs concurrently with per-source error
isolation: one dead board does not stall a poll.

**Deduplication.** Three layers — exact fingerprint, canonical URL, then fuzzy
title match — because the same posting routinely appears on an aggregator, the
company's own board and its ATS.

**ATS checking.** PDF and DOCX parsing with layout signals, then a set of
deterministic rules producing a 0–100 compatibility report and keyword coverage
against a skill vocabulary. No model call is involved in the verdict.

**Scoring.** Two tiers. A free local pre-filter drops obvious mismatches, then
surviving postings get a cached-prompt model call. Prompt caching keeps repeat
scoring cheap.

**CV tailoring, gated.** Tailored resumes must pass a verification step that
checks every claim against your source resume and public GitHub repositories.
Invented metrics, unearned credentials and unsupported skills are blocked, not
flagged. The gate sits above the manual override, so it cannot be forced.

**Applying.** A layered submit gate, a SmartRecruiters API submitter, and a
manual path with pre-filled answers for boards without an API.

**Notifications and prep.** Daily digest, immediate alerts on high scores, and
generated interview prep from the posting plus your resume.

**Operations.** Single-process FastAPI app running the dashboard and scheduler
together, `GET /health` for monitoring, and a Docker image with a persistent
volume.

## Install

```bash
git clone https://github.com/StephenJarso/applycanary
cd applycanary
cp .env.example .env        # add ANTHROPIC_API_KEY, set TZ
docker compose up -d
```

Or without Docker:

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python run.py
```

Open http://127.0.0.1:8000, go to **Profile**, upload your resume, set target
titles and locations. Polling starts on its own.

Set `TZ` in `.env`. The digest and refresh jobs run on cron triggers in local
time, and containers default to UTC.

## What has been verified

Everything below was actually executed, not merely reviewed:

- **113 tests pass**, `ruff check` clean.
- **All dashboard routes serve 200** (`/`, `/review`, `/applications`,
  `/profile`, `/sources`, `/health`) against a real database.
- **All 7 job source endpoints are live** with no field drift — the parsers
  match what the APIs currently return.
- **A live ingest works end to end.** Polling Stripe (Greenhouse) and Ramp
  (Ashby) fetched 674 postings and stored 491, correctly deduplicating 183.
- **Dependencies resolve cleanly** from the `requirements.txt` floors.

The first real test run found four genuine bugs, all fixed in this candidate.
The most serious: SQLAlchemy mappers failed to configure, so the app booted and
then raised on its first query. The most important: an invented credential
("MBA") went undetected whenever the resume already contained a real one
("BSc") — a hole in the anti-fabrication gate.

## Known limitations

- **The Docker image has not been built.** Compose and YAML parse, all Python
  compiles, and every `COPY` source exists — but no `docker build` has run.
- **Dependencies are floors, not pins.** They resolved cleanly today; a future
  install could pick different versions. `pip freeze > requirements.lock.txt`
  from a working build if you need reproducibility.
- **Sources depend on undocumented endpoints.** All five connectors use public
  JSON APIs rather than HTML scraping, which is sturdier, but none of these
  endpoints carry a stability guarantee. They can change shape or start rate
  limiting without notice. Two default probe tokens had already gone stale
  between writing and running.
- **The scheduler is in-process.** If the app stops, polling stops. Run it
  somewhere that stays awake.
- **Scoring and tailoring are unexercised against a live model.** Those paths
  need an `ANTHROPIC_API_KEY` and have not been run end to end.

Run `scripts/verify_sources.py` before relying on the connectors — it takes
seconds and tells you whether a board changed shape.

## Verifying sources

```bash
python3 scripts/verify_sources.py          # stdlib only, runs before install
python3 scripts/verify_sources.py --json
```

Exit status is 1 if any probe fails. "Field drift" means a connector's parser
needs updating.
