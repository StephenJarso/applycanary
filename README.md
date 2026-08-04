# Job Matcher

Self-hosted job discovery, ATS resume tailoring, and application tracking. Runs on
your own machine, polls job boards around the clock, scores openings against your
resume, and prepares tailored applications for you to approve.

## Quick start

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
cp .env.example .env        # then edit: add ANTHROPIC_API_KEY
.venv/bin/python run.py
```

Open http://127.0.0.1:8000, go to **Profile**, upload your resume, and set your
target titles and locations. Polling starts on its own.

Nothing is submitted anywhere until you approve it. See [Auto-submit](#auto-submit).

## What it does

**Finds jobs.** Curated company boards (Greenhouse, Lever, Ashby, SmartRecruiters,
Workable) are polled every 5 minutes; that tight loop is what gets you in early on
a new posting. Broader aggregators (RemoteOK, Adzuna, Hacker News hiring threads)
are polled every 30 minutes. Add companies in `companies.yaml`.

**Deduplicates.** The same job cross-posted to four boards should appear once.
Three layers run in order: exact URL match, a normalized `company|title|location`
key, then fuzzy title similarity within a company. Details in
`app/pipeline/dedup.py`.

**Scores in two tiers.** Every job runs through free local filters first — hard
knockouts (excluded companies, salary floor, remote-only) and keyword overlap
against your resume. Only survivors go to the LLM for real reasoning about fit.
The job description is the cached prefix in each request, so re-scoring against
the same posting is cheap.

**Checks ATS compliance.** A deterministic rule engine, not a language model,
checks the things that actually break resume parsers: multi-column layouts,
tables, images, missing section headers, keyword gaps against the job description,
unparseable dates. Rules live in `app/pipeline/ats_rules.py`.

**Tailors your CV — truthfully.** When your resume falls short, the tailoring pass
rewrites it against the job description using your public GitHub repos as evidence.
Then a separate truthcheck pass compares every claim in the draft against your
original resume and GitHub data. A draft that fails truthcheck is blocked from
submission, not flagged for you to catch later. This is the constraint that keeps
the feature honest: it can surface real skills you under-sold, and it cannot invent
experience you do not have.

**Prepares applications.** Renders a plain, ATS-safe resume plus a cover letter,
and answers standard form fields from your profile.

**Emails you.** A daily digest of what was found, scored, and applied to, plus
immediate alerts for exceptional matches (default: score >= 90).

**Interview prep.** Per-job likely questions, talking points drawn from your actual
projects, and questions to ask them.

## Auto-submit

Off by default. Every prepared application waits in **Review** for you.

Turning on `ENABLE_AUTO_SUBMIT=true` lets the scheduler submit applications scoring
at or above `AUTO_SUBMIT_MIN_SCORE` (default 80) without asking, capped at
`DAILY_APPLY_CAP` per 24 hours. Before you enable it, sit with the review queue for
a few days and read what it produces. An automated application is still an
application with your name on it, and a bad one is not free — some companies keep
a permanent record.

The truthcheck gate applies in both modes. Nothing unverified goes out either way.

## Configuration

Everything is environment variables; see `.env.example` for the annotated list.
The ones that matter most:

| Variable | Default | Notes |
|---|---|---|
| `ANTHROPIC_API_KEY` | — | Without it, scoring is keyword-only and tailoring is off |
| `GITHUB_USERNAME` | — | Evidence source for tailoring |
| `ENABLE_AUTO_SUBMIT` | `false` | See above |
| `AUTO_SUBMIT_MIN_SCORE` | `80` | Only with auto-submit on |
| `DAILY_APPLY_CAP` | `20` | Hard ceiling per 24h |
| `POLL_CURATED_MINUTES` | `5` | Tighten at your own risk of rate limits |
| `SMTP_HOST` / `SMTP_USER` / `SMTP_PASSWORD` / `DIGEST_TO` | — | Unset means digests are logged, not sent |
| `HOST` | `127.0.0.1` | Read the security note before changing |

## Running 24/7

The scheduler runs in the same process as the web app, so the app must stay up.
On a laptop that sleeps, it pauses with the machine. For genuine round-the-clock
coverage, run it on a box that stays awake — a cheap VPS or a Raspberry Pi is
plenty — under systemd:

```ini
[Unit]
Description=Job Matcher
After=network-online.target

[Service]
WorkingDirectory=/home/YOU/job_matcher
ExecStart=/home/YOU/job_matcher/.venv/bin/python run.py
Restart=always
User=YOU

[Install]
WantedBy=multi-user.target
```

`GET /health` reports scheduler state and job counts for external monitoring.

## Security

There is no login screen. The database holds your resume, contact details, and
application history, and `.env` holds your API key. The app binds `127.0.0.1` for
that reason. If you need remote access, put it behind a reverse proxy with
authentication or reach it over a VPN or SSH tunnel — do not bind `0.0.0.0` and
leave it open. Startup warns you if `HOST` is not loopback.

Everything stays local. Job descriptions and your resume go to the Anthropic API
for scoring and tailoring; nothing else leaves the machine.

## Boards and terms of service

Curated connectors use documented public JSON APIs. Aggregator connectors are
polite: identified user agent, conservative intervals, no parallel hammering.
Some job sites prohibit automated access in their terms, and some ATS platforms
prohibit automated submission. Those terms are yours to honor. `companies.yaml`
is where you decide who to poll.

## Tests

```bash
.venv/bin/python -m pytest -q
```

Covers dedup collision behavior, ATS rule checks, truthcheck rejection of
fabricated claims, connector parsing against recorded fixtures, and the scoring
knockout gates. Tests make no network calls.

## Layout

```
app/
  config.py         settings
  models.py         SQLModel tables
  scheduler.py      APScheduler wiring
  sources/          one module per job board
  pipeline/         dedup, score, ats_rules, github, tailor, interview
  resume/           parse, render
  apply/            form fill, submitters, runner
  notify/           email digest and alerts
  web/              routes and templates
companies.yaml      boards to poll
run.py              entrypoint
```
