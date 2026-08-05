#!/usr/bin/env bash
# Build the initial git history for job_matcher as a sequence of small,
# dependency-ordered commits rather than one bulk import.
#
# Usage:
#   bash scripts/git_init_history.sh              # commit, add the default origin
#   bash scripts/git_init_history.sh <url>        # commit, add a different origin
#   bash scripts/git_init_history.sh none         # commit locally, add no remote
#
# Requires git (not installed in the authoring environment -- install with
# `sudo apt install git` on Debian/Ubuntu).
#
# Commit dates are real. This script deliberately does not set GIT_AUTHOR_DATE
# or GIT_COMMITTER_DATE: GitHub surfaces repo creation and push timestamps
# independently of commit dates, so backdated history is both visible and
# misleading to anyone evaluating the work.

set -euo pipefail

REMOTE_URL="${1:-git@github.com:StephenJarso/job-matcher.git}"
[ "$REMOTE_URL" = "none" ] && REMOTE_URL=""
cd "$(dirname "$0")/.."

if ! command -v git >/dev/null 2>&1; then
  echo "error: git is not installed." >&2
  echo "  Debian/Ubuntu:  sudo apt install git" >&2
  exit 1
fi

if [ -e .git ]; then
  echo "error: .git already exists here; refusing to re-initialise." >&2
  echo "  Inspect the existing history with 'git log --oneline' first." >&2
  exit 1
fi

# Fail loudly rather than committing a secret or personal data.
for unsafe in .env data .venv; do
  if [ -e "$unsafe" ]; then
    if ! grep -qE "^${unsafe}/?$" .gitignore 2>/dev/null; then
      echo "error: '$unsafe' exists but is not in .gitignore; aborting." >&2
      exit 1
    fi
  fi
done

git init -q
git checkout -q -b main

# Identity as supplied, set repository-local rather than --global so this does
# not alter git behaviour for any other project on the machine.
git config user.name "stephenjarso"
git config user.email "stephenjacob815@gmail.com"

commit() {
  local message="$1"; shift
  local staged=0
  for path in "$@"; do
    if [ -e "$path" ]; then
      git add -- "$path"
      staged=1
    else
      echo "  warn: missing path '$path' (skipped)" >&2
    fi
  done
  # Nothing new to commit (e.g. a path was already staged) -- skip silently.
  if [ "$staged" -eq 0 ] || git diff --cached --quiet; then
    echo "  skip: $message"
    return 0
  fi
  git commit -q -m "$message"
  printf '  %s  %s\n' "$(git rev-parse --short HEAD)" "$message"
}

echo "Building history on 'main':"

# --- tooling and project skeleton ------------------------------------------
commit "chore: ignore secrets, virtualenv and runtime data

.env holds the Anthropic key, SMTP password and GitHub token. data/ holds the
SQLite database, uploaded resumes and generated CVs. Neither belongs in the
repository." .gitignore

commit "chore: add project metadata, pytest and ruff config" pyproject.toml
commit "chore: pin runtime and development dependencies" requirements.txt
commit "docs: document configuration and safety gates in .env.example" .env.example

# --- configuration and persistence -----------------------------------------
commit "feat(config): centralise settings with startup warnings

Surfaces misconfiguration at boot: missing API key, unconfigured SMTP, and a
non-loopback HOST on a dashboard that has no authentication." app/__init__.py app/config.py

commit "feat(db): SQLite engine in WAL mode for concurrent read/write

The scheduler writes while the dashboard reads; WAL plus a busy timeout avoids
'database is locked' under normal operation." app/db.py

commit "feat(models): job, score, application and telemetry schema

JobAlias keeps duplicate sightings instead of discarding them, so dedup stays
auditable. SourceRun records every connector execution so a silently broken
source is visible rather than merely quiet." app/models.py

# --- text normalisation and dedup ------------------------------------------
commit "feat(pipeline): text normalisation for company, title and location

Deliberately conservative: over-normalising titles collapses genuinely distinct
requisitions, which hides real openings." app/pipeline/__init__.py app/pipeline/normalize.py

commit "test(normalize): cover fingerprint collisions and near-misses" tests/conftest.py tests/test_normalize.py
commit "feat(pipeline): three-layer job deduplication

Fingerprint, then canonical URL, then fuzzy title within a company. Scope and
level words block the fuzzy match so 'Engineer' never merges with
'Engineering Manager'." app/pipeline/dedup.py
commit "test(dedup): verify layer precedence and alias recording" tests/test_dedup.py

# --- source connectors -----------------------------------------------------
commit "feat(sources): connector base class, registry and HTML flattening

Retries, timeouts and error isolation live in the base so each connector stays
a thin parser." app/sources/base.py

commit "feat(sources): Greenhouse public job board connector" app/sources/greenhouse.py
commit "feat(sources): Lever postings connector" app/sources/lever.py
commit "feat(sources): Ashby connector with structured compensation parsing" app/sources/ashby.py
commit "feat(sources): SmartRecruiters connector with bounded detail fetching" app/sources/smartrecruiters.py
commit "feat(sources): RemoteOK aggregator connector

Salary ranges are self-reported, so they are flagged as estimates and never
used to reject a job." app/sources/remoteok.py
commit "feat(sources): register connectors on package import" app/sources/__init__.py
commit "test(sources): parse recorded payload shapes without network" tests/test_sources.py
commit "chore: add curated company board list" companies.yaml
commit "feat(scripts): smoke-test every source endpoint before relying on it

Standard library only, so it runs before dependencies are installed." scripts/verify_sources.py

commit "feat(pipeline): concurrent ingestion with per-source error isolation

One dead board must never stall a poll cycle." app/pipeline/ingest.py

# --- resume parsing and ATS ------------------------------------------------
commit "feat(resume): extract text and layout signals from PDF and DOCX

Real ATS parsers fail on structure more often than wording, so columns, tables
and header/footer text are detected explicitly." app/resume/__init__.py app/resume/parse.py

commit "feat(pipeline): deterministic ATS compatibility rules

No LLM: the user edits a real document based on this output, so the same input
must always produce the same verdict." app/pipeline/ats_rules.py

commit "test(ats): confirm a clean resume stays clean

A checker that flags everything is as useless as one that flags nothing." tests/test_ats_rules.py

commit "feat(pipeline): skill vocabulary and keyword coverage

ATS matching is literal, so a curated vocabulary beats embeddings here and
avoids a heavy compiled dependency." app/pipeline/keywords.py

commit "feat(resume): render ATS-plain DOCX and PDF output

Single column, no tables, no headers -- every one of those is something the
rule engine flags, so generating them would be self-defeating." app/resume/render.py

# --- llm plumbing ----------------------------------------------------------
commit "feat(llm): Anthropic client with prompt caching and JSON extraction

The resume is identical across every job in a scoring cycle, so it belongs in a
cached prefix -- the single biggest cost lever in the pipeline." app/llm/__init__.py app/llm/client.py

commit "feat(llm): scoring, tailoring, cover letter and interview prompts

Static blocks are module constants because they must stay byte-identical for
the cache to hit." app/llm/prompts.py

# --- scoring ---------------------------------------------------------------
commit "feat(pipeline): two-tier scoring with local pre-filtering

Hard filters and keyword coverage run free and locally; only survivors reach
the model. Polling thousands of postings through an LLM would cost more than
the tool saves." app/pipeline/score.py

# --- evidence, tailoring, verification -------------------------------------
commit "feat(pipeline): collect verifiable evidence from public GitHub repos

Without evidence, asking a model to 'add missing keywords' is an invitation to
fabricate." app/pipeline/github.py

commit "feat(pipeline): verify tailored resume claims against source material

The prompt forbids fabrication, but a prompt is a request, not a guarantee.
Invented metrics, credentials and unsupported skills are blocked in code; the
checker fails closed, so a crash never reads as approval." app/pipeline/truthcheck.py

commit "test(truthcheck): reject fabrications, allow truthful rewrites

The sharpest tests in the suite: a false pass here puts an invented claim in
front of an employer." tests/test_truthcheck.py

commit "feat(pipeline): tailor CVs per job behind the verification gate

A version that fails truthcheck is never eligible for automated submission." app/pipeline/tailor.py

commit "feat(pipeline): generate interview prep from the posting and resume" app/pipeline/interview.py

# --- apply layer -----------------------------------------------------------
commit "feat(apply): submitter interface with layered safety gates

Dry-run by default, truthcheck enforced above the force flag, daily cap,
minimum score, and no double submission. Centralised so a new backend cannot
accidentally bypass them." app/apply/base.py

commit "feat(apply): manual submitter and pre-filled form answers

Does everything except the final click, which keeps the speed advantage without
automating a submission the platform has not sanctioned." app/apply/manual.py

commit "feat(apply): SmartRecruiters API submitter

The one platform in this source set with a public candidate endpoint. Resume
attachment is left out: the multipart contract could not be verified, and a
guessed shape would fail in a way that looks like a successful submission." app/apply/smartrecruiters.py

commit "feat(apply): separate artifact preparation from submission

Preparation is safe to run automatically on every good match; sending stays
behind the gates." app/apply/runner.py app/apply/__init__.py

# --- notifications ---------------------------------------------------------
commit "feat(notify): daily digest and immediate high-score alerts

Falls back to logging when SMTP is unconfigured, so a missing password never
loses information." app/notify/__init__.py app/notify/email.py

# --- scheduler and web -----------------------------------------------------
commit "feat(scheduler): background polling with guarded jobs

An unhandled exception silently kills an APScheduler job's future runs, which
would look exactly like 'the bot stopped finding jobs'." app/scheduler.py

commit "feat(web): dashboard routes for jobs, review, applications and profile" app/web/__init__.py app/web/routes.py
commit "feat(web): base layout and stylesheet with dark mode" app/web/templates/base.html app/web/static/app.css
commit "feat(web): job list and detail views with score breakdown" app/web/templates/dashboard.html app/web/templates/job_detail.html
commit "feat(web): review queue and applications history views" app/web/templates/review.html app/web/templates/applications.html
commit "feat(web): profile page and connector health view" app/web/templates/profile.html app/web/templates/sources.html

commit "feat(app): FastAPI factory running dashboard and scheduler together" app/main.py
commit "feat: single-command entrypoint binding loopback by default" run.py
commit "docs: README covering setup, safety gates and the auto-submit tradeoff" README.md

# --- anything not explicitly listed above ----------------------------------
git add -A
if ! git diff --cached --quiet; then
  git commit -q -m "chore: add remaining project files"
  printf '  %s  %s\n' "$(git rev-parse --short HEAD)" "chore: add remaining project files"
fi

echo
echo "$(git rev-list --count HEAD) commits on main."
echo
echo "Verify nothing sensitive was committed:"
echo "  git ls-files | grep -E '\\.env$|^data/' || echo '  clean'"

if [ -n "$REMOTE_URL" ]; then
  echo
  echo "Adding origin: $REMOTE_URL"
  git remote add origin "$REMOTE_URL"

  # The target repo already exists and may contain an initial commit (a README
  # created at setup time). That makes this a non-fast-forward push. Detect it
  # and stop, rather than resolving it with --force and destroying whatever is
  # already there.
  echo "Checking remote state..."
  if git ls-remote --exit-code --heads origin main >/dev/null 2>&1; then
    echo
    echo "  Remote 'main' already has commits."
    echo "  A plain push will be rejected as a non-fast-forward."
    echo
    echo "  If the remote only contains an auto-generated README you do not"
    echo "  want to keep, overwrite it deliberately:"
    echo "      git push --force-with-lease -u origin main"
    echo
    echo "  If it has anything you want to keep, reconcile first:"
    echo "      git fetch origin"
    echo "      git rebase origin/main"
    echo "      git push -u origin main"
  else
    echo "  Remote has no 'main' branch; a normal push will work."
    echo
    echo "  Review the history, then push:"
    echo "      git log --oneline"
    echo "      git push -u origin main"
  fi
fi
