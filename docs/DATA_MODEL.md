# Data model

Eight tables, defined in `app/models.py` using SQLModel. Stored in SQLite with
WAL enabled (`app/db.py`) so the scheduler can write while the dashboard reads.

## Relationships

```
   profile  (single row: the job seeker)

   job  1---N  job_alias        every board a posting was seen on
        1---1  job_score        scoring result
        1---N  resume_version   tailored CVs generated for this job
        1---1  application      the submission record
        1---1  interview_prep   generated prep material

   source_run                   independent connector telemetry
```

## Enums

Defined once in `app/models.py` and reused across the pipeline.

**`JobStatus`** — the lifecycle of a posting.

| Value | Meaning |
|---|---|
| `new` | Ingested, not yet scored |
| `scored` | Scored, awaiting a decision |
| `queued` | Artifacts built, awaiting approval or auto-submit |
| `applied` | Submitted |
| `rejected` | Filtered out by scoring |
| `skipped` | User declined |
| `expired` | No longer present at source |
| `failed` | Submission errored |

**`ApplyMethod`** — how an application was sent.

| Value | Meaning |
|---|---|
| `api` | Submitted through a sanctioned public endpoint |
| `manual` | Review queue; a human clicked submit |
| `dry_run` | Prepared but deliberately not sent |

`dry_run` exists as a first-class value so dry runs appear in history and are
distinguishable from real submissions when auditing what the system did.

**`Severity`** — ATS finding weight: `critical` (a parser will likely drop or
garble the resume), `warning`, `info`.

## Tables

### `profile`

Single row. Identity and contact fields used to pre-fill application forms, plus
the base resume (`base_resume_path`, `base_resume_text`) and search preferences
(`target_titles`, `target_locations`, `min_salary`, `remote_only`,
`excluded_companies`, `work_authorization`).

`skills` and `github_evidence` are JSON columns holding the parsed skill list and
the cached GitHub scan. `github_synced_at` records the last scan so it can be
refreshed on a schedule rather than per request.

### `job`

One row per unique posting after deduplication.

`fingerprint` is the layer-1 dedup key — a hash of normalised company, title, and
location. `canonical_url` is the layer-2 key, with tracking parameters stripped.

`source` and `source_id` identify where the posting was first seen;
`ats_platform` and `ats_board_token` determine which submitter can handle it.
`salary_is_estimate` marks a parsed-not-stated salary, and such values never
reject a posting.

`first_seen_at` and `last_seen_at` bound the posting's observed lifetime;
`seen_count` counts sightings across polls. The `age` property derives time
since posting, which drives the apply-early prioritisation.

Indexed on `(status, posted_at)` for the dashboard queue and
`(source, source_id)` for ingestion lookups.

### `job_alias`

Every additional place a posting was seen. Duplicates are recorded rather than
discarded, which makes deduplication auditable: `matched_by` names the layer that
matched (`fingerprint`, `canonical_url`, or `fuzzy_title`) and `match_score`
carries the similarity where relevant.

Unique on `(source, source_id)` so re-polling the same board cannot create
duplicate aliases.

### `job_score`

One row per job. Component scores are kept separately rather than collapsed:
`keyword_score` (tier 1, local), `semantic_score` (tier 2, model), `ats_score`
(resume structure against this description), and the combined `total`.

`decided_by` records which stage produced the verdict — `tier1_filter`,
`tier1_keyword`, or `tier2_llm` — and `disqualifier` holds the reason when a hard
filter rejected the posting. Together they explain any score without re-running
it, which matters because most postings are eliminated locally and never reach a
model.

`matched_keywords` and `missing_keywords` are JSON lists; `model_used` records
the model for cost attribution and for interpreting old scores after a model
change.

### `resume_version`

A CV generated for a specific job. Holds `docx_path`, `pdf_path`, extracted
`text`, and `diff_summary`.

`ats_score_before` and `ats_score_after` quantify whether tailoring actually
improved parseability. `keywords_added` lists terms introduced.

The verification fields are load-bearing: `truthcheck_passed` gates automated
submission, `unverifiable_claims` lists the specific blocking claims, and
`truthcheck_notes` carries the detail. A version failing this check is never
eligible for automatic submission — see [SAFETY.md](SAFETY.md).

### `application`

One row per job. `method` records how it was sent, `submitted_at` is null until
it actually is, and `attempts` counts tries.

`form_answers` is a JSON blob of pre-filled responses for the manual queue.
`confirmation` and `error` capture the outcome. `follow_up_due`,
`response_received`, and `outcome` support tracking after submission.

### `interview_prep`

Generated prep for a job: `technical_questions` and `behavioural_questions` as
JSON lists, plus `questions_to_ask`, `company_notes`, and `skill_gaps` —
requirements with no supporting evidence in the user's background, which is the
honest answer to what to study before the interview.

### `source_run`

One row per connector execution: `duration_ms`, `found`, `new_jobs`,
`duplicates`, `ok`, and `error`.

This table exists because a broken connector fails silently. A board that starts
returning zero postings looks exactly like a quiet hiring week. Recording every
run lets the dashboard surface a source that has stopped producing results.

## Conventions

- Timestamps are naive UTC via `utcnow()` in `app/models.py`. SQLite has no
  timezone-aware type, and mixing aware and naive datetimes is a reliable source
  of comparison bugs.
- List and dict fields are JSON columns. At single-user scale, normalising
  keyword lists into separate tables would add joins without benefit.
- Long free text (`description`, `reasoning`, `text`) uses `Text` rather than the
  default `String`.
- There are no migrations. `init_db()` calls `create_all`, which creates missing
  tables but does not alter existing ones. Changing a column on a populated
  database currently means recreating it — see
  [OPERATIONS.md](OPERATIONS.md#schema-changes).
