# Safety model

The irreversible action in this system is sending an application to a real
employer under the user's name. A bad automated application is not free: it can
burn a genuine opportunity, and some companies keep a permanent candidate
record. Everything here follows from that.

Two independent mechanisms:

1. **Truthfulness** — generated CV content is verified against source material
   in code, not merely requested in a prompt.
2. **Submission gates** — every path to sending passes through one centralised
   check, and automation is off by default.

---

## 1. Truthfulness

### The problem

Tailoring a CV to a job description means asking a model to rewrite a person's
history to better match what an employer asked for. That is one short step from
inventing experience. The failure mode is specific and serious: a fabricated
claim reaches an employer, the candidate cannot defend it in an interview, and
the consequence — a withdrawn offer, or dismissal after hiring — lands on them,
not on the tool.

A prompt saying "do not invent metrics" is a request. It is not a guarantee.

### The mechanism

`app/pipeline/truthcheck.py` verifies every tailored claim against two sources
of truth: the user's original resume, and the GitHub evidence gathered by
`app/pipeline/github.py`.

Three categories block:

| Category | What it catches |
|---|---|
| Invented metrics | A percentage, dollar figure, or headcount absent from the source |
| Invented credentials | Degrees, certifications, clearances not held |
| Unsupported skills | Technologies with no evidence in resume or repositories |

**The checker fails closed.** If verification raises an exception, the result is
a blocking violation, not a silent pass. An error in the safety mechanism must
never read as approval.

Results are persisted on `resume_version`: `truthcheck_passed` gates submission,
`unverifiable_claims` lists the specific blocking claims, and `truthcheck_notes`
carries detail for the review UI.

`tests/test_truthcheck.py` is the most important test file in the repository. It
asserts in both directions: fabrications are rejected, and legitimate rewrites
of true content are not. A checker that blocks everything is as useless as one
that blocks nothing.

### What tailoring may still do

Rewording, reordering, and re-emphasis are legitimate — surfacing real
experience the original resume buried is the actual value of the feature. The
line is between *presenting* true things well and *asserting* untrue things.

---

## 2. Submission gates

All gates live in `SubmitGate.check` (`app/apply/base.py`) rather than in each
submitter, so a new backend cannot accidentally omit one.

Evaluated in order:

| # | Gate | Behaviour when it trips |
|---|---|---|
| 1 | Already applied | Blocks — never submit twice to the same job |
| 2 | Truthcheck failed | Blocks — **not overridable** |
| 3 | `ENABLE_AUTO_SUBMIT` false | Downgrades to dry run |
| 4 | Daily cap reached | Blocks |
| 5 | Below minimum score | Blocks |
| 6 | Not yet scored | Blocks |

### Ordering is deliberate

The truthcheck gate is evaluated **before** the `force` branch. `force` exists
so the user can approve a submission from the review UI, bypassing the score
threshold and the auto-submit flag. It does not bypass verification. There is no
supported path — API or UI — that submits a CV containing claims the system
could not verify.

### Defaults

`ENABLE_AUTO_SUBMIT` defaults to `false`. Out of the box the system finds,
scores, tailors, and queues, and every submission waits for a human. Turning it
on is a deliberate edit to `.env`.

`DAILY_APPLY_CAP` bounds the blast radius of a bug. If scoring misbehaves and
starts rating everything highly, the cap limits the damage to one day's quota
rather than the entire job board.

`AUTO_SUBMIT_MIN_SCORE` (default 80) keeps marginal matches in the review queue
even when automation is on.

### Dry runs are recorded

`ApplyMethod.DRY_RUN` is a first-class value. Dry runs appear in history and are
distinguishable from real submissions, so it is always possible to audit what
the system actually sent versus what it merely prepared. The daily-cap query
explicitly excludes dry runs from the count.

---

## 3. Platform boundaries

Only SmartRecruiters supports automated submission, because it is the one
platform in this source set with a public candidate API. Everything else routes
to the manual queue via `app/apply/manual.py`, which prepares the CV, cover
letter, and pre-filled form answers, then stops.

This is a deliberate limit, not a gap awaiting a browser-automation backend.
Driving a login-gated application form with headless browser automation would
mean acting as the user against a platform that has not sanctioned it, and would
put their account at risk.

### The unimplemented attachment

`app/apply/smartrecruiters.py` does not attach the resume file. The multipart
contract could not be verified against the live API, and a guessed encoding
fails in the worst available way: the request succeeds, the application is
recorded as submitted, and it arrives with no CV.

Not implementing it is the safer choice. This is documented rather than hidden
so it is fixed intentionally, after checking the real API, rather than
discovered when an application silently arrives empty.

---

## 4. Data and credentials

**The dashboard has no authentication.** It exposes the resume, scores, and full
application history. `app/config.py` emits a startup warning when `HOST` is not
loopback. Do not expose it to a network without authentication in front of it.

Secrets live in `.env`, which is gitignored: `ANTHROPIC_API_KEY`,
`SMTP_PASSWORD`, `GITHUB_TOKEN`. `data/` is gitignored too — it holds the
database, uploaded resumes, and generated CVs.

If a token is ever pasted into a chat, a log, a screenshot, or a commit,
treat it as compromised and revoke it. Rotation is cheap; a leaked token with
repository scope is not.

The GitHub scan reads public repository data only and requires no write scope.

---

## 5. Operating recommendation

Run with `ENABLE_AUTO_SUBMIT=false` for the first week and read what the review
queue produces. Check whether scores match your own judgement of fit, and read
several tailored CVs end to end before trusting the pipeline to send one.

An automated application still carries your name. The system is built so the
default is to prepare and wait.
