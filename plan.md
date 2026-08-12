# ApplyCanary — Multi-User Retrofit & Deployment Plan

**Status as of this document:** Phases 1 (auth foundation + request plumbing)
and 2 (data migration) are **complete and verified**. Phases 3–6 below are
**not started**.

Branch: `worktree-deploy-app-standalone-release`

---

## Why this work exists

A GitHub release ships downloadable files; it hosts nothing. Making ApplyCanary
reachable by other people needs a deployment **and** real user accounts, because
the app was architecturally single-user. Deploying it as-is would have put every
visitor inside the owner's account — same résumé, same contact details, same
application history.

The single-user assumption was load-bearing in the schema, not a config flag:

| Problem | Location | Consequence |
|---|---|---|
| No `User` model at all | `app/models.py` | Nothing to scope data by |
| `check_request_authenticated` returned `bool` | `app/auth.py:89` | Discarded the username it had just recovered — no "current user" existed |
| Plaintext password comparison | `app/auth.py:76-77` | Credentials stored unhashed in config |
| `Job.status` on a **shared** table | `app/models.py:128` | One user's skip/apply mutated the row everyone saw |
| `score_pending` selects `status == NEW`, then flips it | `app/pipeline/score.py:264,303` | **First user to score a job removed it from every other user's queue permanently** |
| `unique=True` on `job_id` ×3 | `JobScore:177`, `Application:231`, `InterviewPrep:256` | Second user to score any job hit a constraint violation |
| `select(Profile).first()` in 13 places | across `app/` | Every read returned user #1's data |
| Global `daily_apply_cap` count | `app/apply/base.py:89-95` | One user's submissions starved everyone |
| Shared résumé path `resume_dir/base{suffix}` | `api.py:516`, `routes.py:464` | **Every upload silently overwrote the previous user's résumé** |
| IDOR on résumé download | `api.py:567` | `session.get(ResumeVersion, id)` with no ownership check — incrementing the URL id disclosed other users' résumés |
| `secret_key` defaulted to a committed literal | `config.py:95` | Anyone reading the public repo could forge a session cookie for any account |
| `/health` exposed per-user counts | `routes.py:384` | Unauthenticated endpoint leaked pipeline state |

**Decisions taken:** shared `Job` + per-user `UserJob` (postings scraped once,
serve everyone); invite-only signup (operator's API keys pay for every user);
Fly.io with SQLite on a persistent volume.

---

## Phase 1 — COMPLETE

### Schema (`app/models.py`)

New tables:

```
User        id, email (unique), password_hash, is_active, is_admin,
            token_version, created_at, last_login_at
InviteCode  id, code (unique), created_by_id, used_by_id, used_at,
            expires_at, created_at  +  is_redeemable()
UserJob     id, user_id, job_id, status, created_at
            UNIQUE(user_id, job_id) + INDEX(user_id, status)
```

Changed:
- `Profile` — added `user_id` (FK, unique: one profile per user) and the
  per-user preferences that used to be process-wide settings:
  `alert_min_score`, `auto_submit_min_score`, `daily_apply_cap`,
  `enable_auto_submit`, `digest_to`.
- `Job` — **removed** workflow `status`; added `expired_at` for the one
  genuinely global transition. Index `ix_job_status_score` narrowed to
  `posted_at`.
- `JobScore` / `Application` / `InterviewPrep` — added `user_id`; replaced
  `unique=True` on `job_id` with composite `UNIQUE(user_id, job_id)`.
- `ResumeVersion` — added `user_id`.
- Relationships: `Job.score`/`Job.application` were scalars with
  `uselist: False`. They are now **collections** (`scores`, `applications`,
  `user_states`) — one row per user. Handlers must select the requesting user's
  row rather than reading `job.score` directly, which would return whichever row
  the ORM loaded first.

### Auth (`app/auth.py` — rewritten)

- `hash_password` / `verify_password` using **stdlib `hashlib.scrypt`**
  (n=2¹⁴, r=8, p=1, 16-byte salt). Chosen over bcrypt/argon2: memory-hard,
  no compiled dependency to install in the image or CI, and Python ≥3.12 is
  already required. Format `scrypt$n$r$p$salt$hash` carries its parameters so
  they can be raised later without invalidating stored passwords.
- Session token now carries `user_id:token_version:timestamp:hmac`.
  `token_version` bumps on password change, so a stolen cookie stops working
  instead of outliving the password for its full 30-day life.
- `parse_session_token` verifies signature + age only; existence, active state
  and version are checked against the database in `resolve_current_user` — a
  valid signature alone is never sufficient.
- `authenticate()` hashes a dummy password on unknown accounts so a missing
  email and a wrong password take the same time (no account enumeration).

### Request plumbing

- `app/deps.py` (new): `current_user`, `current_profile`, `require_admin`, and
  `user_job(session, user_id, job_id)` — the accessor that replaces every
  `job.status` read, treating a missing row as `NEW`.
- `app/main.py` middleware resolves identity **once** and stashes
  `request.state.user_id` before routing, so dependencies and the SPA
  catch-alls share one lookup. `/register` added to the bypass list.
- `config.py`: `startup_errors()` **refuses to boot** when a non-loopback bind
  still carries the default `SECRET_KEY`. `session_cookie_secure` defaults on
  for any public bind.
- `routes.py`: real `/login` (email + password), `/register` (invite-gated,
  ≥10-char password, creates the Profile row), `/logout`. Cookie now sets
  `secure` on public binds. Invite is marked spent **only after** the account
  commits, so a failure leaves it usable.
- Templates: extracted `auth_base.html` (the login page had ~80 lines of inline
  CSS); `login.html` and new `register.html` extend it.

### Scoping applied (all 23 `JobStatus` sites, 8 files)

`api.py`, `routes.py`, `score.py`, `tailor.py`, `interview.py`, `runner.py`,
`base.py`, `scheduler.py` — every per-user query filters on `user_id`;
`Job.status` reads route through `UserJob`; `_counts`/`_profile` helpers take a
user. `score_pending` now selects candidates via an **outer join** on `UserJob`
(no row **or** status NEW), which is what decouples users from each other.
`dedup.py` keeps a plain `Job.expired_at` check — expiry is genuinely global.

Also fixed in passing: the IDOR (ownership check, 404 not 403 so id existence
isn't confirmed), per-user résumé directories (`resume_dir/user_{id}/`), the
global daily-cap count, and `/health` no longer returning per-user tallies.

### Scheduler fan-out (`app/scheduler.py`)

`_active_profiles()` helper. `score_new`, `prepare_queue`, `auto_submit`,
`refresh_github`, `digest` loop over active users. `poll_curated`/`poll_broad`
stay global — scrape once, serve all. `expire_stale` rewritten: it sets
`Job.expired_at` and no longer needs a status exclusion list, so one user's
QUEUED can't pin a posting for everyone.

### Verification performed

- `pytest`: **185 passed**
- `ruff check app/ scripts/`: clean
- `create_app()` imports successfully
- `scripts/verify_isolation.py` (new): asserts two users can score the same job,
  that A skipping doesn't change B's status, and that a B-scoped query can't see
  A's score.

---

## Phase 2 — Data migration (COMPLETE) — done before any deploy

`sync_schema()` (`app/db.py:93-136`) is additive-only and **explicitly skips
NOT NULL columns without a default** (`db.py:120-126`). It also cannot alter a
unique constraint, which in SQLite requires a full table rebuild. The new
`user_id` columns and composite uniques therefore **will not** appear on the
existing database by themselves.

Write `scripts/migrate_multiuser.py`:

1. Copy `data/applycanary.db` to a timestamped backup; **verify the copy opens**
   and abort if not.
2. Delete the three "John Doe" fixture profiles (ids 2–4) so they don't become
   orphaned accounts.
3. Create `user`, `invite_code`, `user_job`.
4. Create the owner account from `AUTH_USERNAME`, **prompting for a new
   password** — the current one is plaintext in `.env` and must not be reused.
   Set `is_admin=True`.
5. Add `user_id` columns; backfill every existing row to the owner
   (1810 jobs, 286 applications, 21 résumé versions, 22 interview preps).
6. Populate `user_job` from the current `job.status` values for the owner;
   set `job.expired_at` where status was EXPIRED.
7. Rebuild `job_score`, `application`, `interview_prep` with the composite
   uniques (create → copy → drop → rename) inside one transaction.
8. Print row counts before/after and **assert they match**.

Rehearse against a **copy** first. Confirm 1810 jobs and 286 applications
survive and all belong to the owner.

**Done (2026-08-12):** `scripts/migrate_multiuser.py` written, rehearsed on a
copy, then run against the live database. All row counts survived the rebuild
(job 1896, job_score 1810, application 286, interview_prep 24, resume_version
71, job_alias 2147, source_run 1943); the three fixture profiles were removed;
`PRAGMA foreign_key_check` clean; composite uniques verified on all four
per-user tables. Pre-migration backup saved at
`data/applycanary.multiuser-<timestamp>.db`. Owner account is `admin`
(password set at migration time, hashed with scrypt — **not** the old
`AUTH_PASSWORD`); `sync_schema` adds the per-profile preference columns on
first boot.

---

## Phase 3 — Frontend auth (NOT STARTED)

`frontend/src` currently has **zero** references to login, logout, or session —
the SPA relies entirely on the Jinja pages.

- Login + register pages mirroring the server-rendered flow.
- 401 interceptor in the react-query client → redirect to `/login`.
- Logout control in the nav.
- `api.ts`: the `JobDetail` type still assumes one score/application per job.

---

## Phase 4 — Test coverage for the new invariants (NOT STARTED)

`tests/test_auth.py` was rewritten for the user model (15 tests). Still needed:

- Two users scoring the same job through the **HTTP layer** (not just the ORM).
- User B fetching `/api/resume-versions/{A's id}/download/docx` → 404.
- `daily_apply_cap` enforced per user.
- Scheduler fan-out touching every active user.
- `startup_errors()` refusing to boot on a public bind with the default key.

---

## Phase 5 — Fly.io deployment (NOT STARTED)

The Dockerfile is already production-shaped: multi-stage, non-root (`uid 10001`),
`/data` volume, healthcheck. It maps onto Fly with little change.

`fly.toml` needs:
- Volume mounted at `/data` (SQLite + résumés + artifacts).
- `min_machines_running = 1`, **auto-stop disabled** — APScheduler dies with the
  machine, and a stopped machine means no polling or scoring.
- `force_https = true`.
- Health check → `/health`.

Secrets via `fly secrets set` — never baked into the image:
`SECRET_KEY` (random, ≥32 bytes), `AUTH_*`, `GEMINI_API_KEY`, `OPENROUTER_API_KEY`,
`GROQ_API_KEY`, `SMTP_*`.

Sequence: `fly launch --no-deploy` → create volume → set secrets → deploy →
**run the migration against the volume** → create the first invite code → verify
`/health` and `fly logs` show the scheduler started.

---

## Phase 6 — Operational follow-ups (NOT STARTED)

- **Per-user LLM budget.** Your Gemini free tier was exhausted by *one* user, and
  the backlog rescore is still incomplete. N users means N× spend on the same
  keys. Add a per-user daily cap before inviting anyone.
- **Admin UI for invite codes** — currently only mintable via the database.
- **Password reset** — no flow exists; a forgotten password needs manual DB work.
- **Rate-limit `/login` and `/register`** — the only unauthenticated write
  surfaces.
- **Data obligations.** Hosting other people's résumés, contact details and
  application history brings retention/deletion/breach responsibilities a
  personal tool didn't have. Consider account deletion and a short privacy note.

---

## Known limitations

- SQLite on one volume ⇒ single machine, no horizontal scale. Postgres is the
  upgrade path.
- Scheduler fan-out is linear in user count inside a single process
  (`main.py:65`); it will need a queue well before it needs Postgres.
- `Job.expired_at` replaced a status value, so anything reading the old
  `JobStatus.EXPIRED` from an external script (e.g. `scripts/rescore_all.py`,
  which still references `Job.status`) needs updating.
