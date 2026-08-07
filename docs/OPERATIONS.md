# Operations

Running, tuning, and debugging the system. Setup and configuration reference live
in the [README](../README.md); this covers what to do once it is running.

## Verify before trusting

The source connectors were written against documented API shapes and tested
against recorded payloads, not against live endpoints. Before relying on any of
them:

```bash
python scripts/verify_sources.py
```

This hits each connector's real endpoint and reports what came back. A connector
returning zero postings is the failure to care about — it is indistinguishable
from a quiet hiring week unless you look.

Then run the suite and the linter:

```bash
pytest -q
ruff check .
```

## First week

Leave `ENABLE_AUTO_SUBMIT=false` and read the output.

1. Check the jobs list. Are these postings you would actually apply to? If the
   list is full of irrelevant roles, the problem is your target titles and
   locations on the profile page, not the scoring model.
2. Compare scores against your own judgement on ten postings. Systematic
   disagreement means the tier-1 keyword vocabulary in
   `app/pipeline/keywords.py` is missing terms from your field.
3. Read several tailored CVs end to end. This is the check that matters most —
   see [SAFETY.md](SAFETY.md).
4. Look at the sources page for connectors that have gone quiet.

Only then consider turning automation on.

## Tuning

Search preferences — target titles, locations, minimum salary, excluded
companies, remote-only — live on the profile record and are edited on the
profile page, not in `.env`. Only operational settings (auto-submit, caps,
credentials, host) are environment variables.

**Too few jobs.** Widen your target titles, add boards to `companies.yaml`, or
relax the minimum salary. Check the sources page first — a silently broken
connector looks identical to a narrow search.

**Too many irrelevant jobs.** Tighten target titles and add to excluded
companies. These are cheap local filters, so tightening them also cuts model
spend.

**Scores cluster too high or too low.** Tier 1 gates what reaches the model at
all. If genuinely good matches are being rejected before tier 2, the keyword
vocabulary is the cause. If everything scores highly, the rubric is not
discriminating — raise `AUTO_SUBMIT_MIN_SCORE` rather than trusting the spread.

**Duplicates getting through.** Check `job_alias` to see which layer matched. If
near-identical titles slip past layer 3, the fuzzy threshold is too strict. If
distinct requisitions are being merged, it is too loose — that direction is worse,
because a merged job is an opening you never see.

**Cost higher than expected.** Tier 2 is the only paid path. Confirm tier 1 is
eliminating most postings: `decided_by` on `job_score` shows which stage decided
each one. A high proportion of `tier2_llm` means the local filters are too
permissive. Also confirm prompt caching is working — the resume prefix is
identical across a cycle and should be a cache hit after the first call.

## When something breaks

**A source returns nothing.** Boards change API shapes without notice. Run
`scripts/verify_sources.py` to see the raw response, then fix that connector's
parser. Because failures are isolated per source, one broken board does not stop
the others.

**Scheduler stopped producing.** An unhandled exception in an APScheduler job
cancels its future runs. Every job in `app/scheduler.py` is wrapped in an error
guard for this reason, but check the logs for a guard that caught something
repeatedly — that is a real bug, not a transient failure.

**`database is locked`.** WAL plus a busy timeout handles normal single-user
operation. If it recurs, something is holding a long write transaction; look for
a session that is not being closed.

**Submissions all dry-run.** Expected unless `ENABLE_AUTO_SUBMIT=true`. The gate
result explains itself — check the reason string on the application record.

**Truthcheck blocking everything.** Read `unverifiable_claims` on the resume
version. Common cause: the base resume is thin, so tailoring has little
verifiable material to work from. Adding a GitHub token widens the evidence base.
Do not work around this by loosening the checker.

## Schema changes

There are no migrations. `init_db()` calls `create_all`, which creates missing
tables but never alters existing ones. Adding a column to a populated database
means either writing the `ALTER TABLE` by hand or recreating the database:

```bash
cp data/job_matcher.db data/job_matcher.db.backup
rm data/job_matcher.db
python run.py
```

That loses history. Back up first. If the schema starts changing often, add
Alembic rather than repeating this.

## Backups

`data/` holds everything not reproducible from the code: the database, uploaded
resumes, and generated CVs. It is gitignored, so it is not in the repository.

```bash
tar czf backup-$(date +%F).tar.gz data/
```

Postings can be re-fetched. Your application history cannot.

## Logs

Logging is configured in `app/main.py`; `LOG_LEVEL` controls verbosity.

Worth watching: source run results, gate decisions with their reasons, and
truthcheck blocks. `DEBUG` logs full prompts and responses — useful when tuning
the rubric, and noisy enough that it is not a default.
