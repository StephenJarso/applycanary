# Render deployment

Render is a good free-tier target for ApplyCanary. The repository already
contains a multi-stage `Dockerfile` and a [`render.yaml`](../render.yaml)
blueprint; Render builds that Dockerfile and runs its `CMD` (`python run.py`) —
the same image used by docker-compose. No build or port
configuration is needed: Render detects the port from the `PORT` env var.

## What the free plan gives you — and what it costs you

Before deploying, know the three free-plan limitations that ApplyCanary hits
(Render's own docs still recommend paid instances for production):

1. **Ephemeral disk — the important one.** Free web services cannot attach
   persistent disks; the filesystem is wiped on every redeploy, restart and
   spin-down. With `DATABASE_URL=sqlite:////data/applycanary.db` you would lose
   the database, uploaded resumes and generated CVs on every deploy.
2. **Spin-down after 15 idle minutes.** The service sleeps with no traffic and
   takes ~1 minute to wake. The background scheduler (job polling, scoring,
   digests) does not run while asleep. One always-on service uses ~744 of the
   750 free instance-hours per month, so keeping it warm with a free pinger
   fits the allowance — but it consumes nearly all of it, leaving no room for
   a second free service. The repo ships a GitHub Actions workflow
   (`.github/workflows/keepalive.yml`) that pings `/health` every 5 minutes;
   see [Keep-alive](#keep-alive) below.
3. **Outbound SMTP ports (25/465/587) are blocked.** Use an HTTP email API —
   the app prefers Brevo (`BREVO_API_KEY`), then Resend (`RESEND_API_KEY`),
   over SMTP when set.

For durable data on the free plan, pair the web service with a hosted
Postgres. **Supabase** is a good pick: free tier, no credit card, 500 MB
Postgres with pgvector for job embeddings:

1. Sign up at [supabase.com](https://supabase.com) and create a project.
2. Open **Project Settings → Database → Connection string** and choose
   **Session pooler**. Copy the URI — it looks like:

   ```text
   postgresql://postgres.<ref>:<password>@aws-0-<region>.pooler.supabase.com:5432/postgres?sslmode=require
   ```

   Use the **Session pooler** (port `5432`), not the transaction pooler on
   `6543`. The session pooler is IPv4, so it also avoids Render's missing IPv6
   route (`Network is unreachable` on AAAA records).

## Create the service

1. Push this repo to GitHub if it is not already there.
2. In the Render Dashboard: **New → Blueprint** and select the repository.
   Render reads `render.yaml` and creates one web service named `applycanary`.
   (No blueprint preference? **New → Web Service** with *Docker* runtime works
   identically; the values below match `render.yaml`.)
3. Under **Secret files / Environment**, fill in the `sync: false` variables
   Render prompts for:
   - `PUBLIC_BASE_URL` — your Render URL, e.g. `https://applycanary.onrender.com`
     (needed for the confirm/reset links inside emails; add after the first
     deploy once the URL is known, then redeploy once). REQUIRED before any
     signup — see the email-verification section below for why.
   - At least one LLM key: `GEMINI_API_KEY` (or `XAI_API_KEY`, `GROQ_API_KEY`,
     `ANTHROPIC_API_KEY`). Without one, scoring falls back to keyword-only.
   - `BREVO_API_KEY` (no domain needed: verify a sender address) or
     `RESEND_API_KEY` (requires a verified domain) for email — verification,
     alerts, digests. REQUIRED when `REQUIRE_EMAIL_VERIFICATION` is on — see
     below for the lockout risk if the gate is on without a mail backend.
4. Click **Apply**. The first build takes a few minutes (frontend `npm build`
   plus the Python venv); subsequent builds cache layers and are faster.

## Data durability: point at Supabase (required on free)

Render's free tier has no persistent disk, so the SQLite file under `/data`
would be wiped on every redeploy and spin-down. The blueprint therefore marks
`DATABASE_URL` as `sync: false` — Render prompts for it at deploy time and you
paste the Supabase connection string from the step above:

1. Paste that string when Render prompts for `DATABASE_URL` during blueprint
   apply (or afterwards under **Environment**), then deploy. Tables are
   created automatically on first connect — no migration step is needed.

2. On Supabase's free plan, note the soft limits: a project **pauses after
   about 7 days without database activity** (restore it from the dashboard if
   that happens) and storage is capped at 500 MB — plenty for this app's data.
   Uploaded resumes and generated CVs live in `/data` on Render's ephemeral
   disk and still vanish on redeploy.

Because the database lives outside the container, deployments and spin-downs
no longer touch your **relational** data (jobs, scores, applications,
users). File artifacts (resumes, generated CVs) are not durable on free —
move to S3 (`S3_BUCKET` via `AWS_*`) if that becomes a problem.

## Health check

The service reports readiness at `/health` (`{"ok":true, "scheduler_running":
true}`). `render.yaml` sets `healthCheckPath: /health`, so a deploy is only
marked live once the scheduler has started. If the health check fails during
deployment, open the service **Logs** tab: a boot error (bad `DATABASE_URL`,
missing variable) shows there verbatim.

## Keep-alive

Free services sleep after 15 idle minutes, which also stops the scheduler. The
included workflow `.github/workflows/keepalive.yml` curls `/health` every 5
minutes once you set a repository variable:

1. Render dashboard → your service → copy the URL
   (`https://applycanary.onrender.com`).
2. GitHub repo → **Settings → Secrets and variables → Actions → Variables →
   New repository variable**: `RENDER_URL` = that origin (no trailing slash).
3. Run the workflow once from the **Actions** tab (`Render keep-alive` →
   **Run workflow**) to confirm it returns `{"ok":true,...}`.

Caveats: GitHub can delay scheduled runs under load, and it disables schedules
in repositories with no activity for 60 days. For a guaranteed ping, point a
free external monitor (cron-job.org, UptimeRobot) at the same `/health` URL.

Keeping the service warm uses ~744 of the 750 free instance-hours per month.

## Email verification (required by the blueprint)

The blueprint ships `REQUIRE_EMAIL_VERIFICATION=true`: a new signup gets no
session until the emailed link is clicked (login then 403s with a resend
prompt). Three rules make this safe — break any one and signups lock out
silently:

1. **A mail backend must be live.** With the gate on and no mail backend,
   the verification email is dropped (`email.py` logs `DROPPED`) and no one
   can ever cross the gate — the resend button fails the same way.
   - **No domain yet → Brevo** (easiest): create a key at
     app.brevo.com → SMTP & API → API Keys, add your address under
     Senders & IP → Senders (Brevo emails you a confirmation link), then set
     `BREVO_API_KEY` and `EMAIL_FROM` to that same address. Free tier: 300/day.
     It is tried first and works for any recipient without owning a domain.
   - **Have a domain → Resend**: set `RESEND_API_KEY` (free tier: 100/day,
     no card) and verify the domain at resend.com/domains.

   Note: a verified sender address on a free mailbox (e.g. Gmail) can land in
   spam because the domain's DMARC does not align; a verified custom domain
   has much better deliverability. Either way, Brevo unblocks signups today.
2. **`PUBLIC_BASE_URL` must be set before any signup.** The verify/reset
   links in emails are built from it (an absolute origin in `email.py`'s
   `_link()`); without it the mail is skipped rather than sent with a dead
   link. Set it the moment the Render URL exists, redeploy once.
3. **Test on yourself first.** Register your own address, confirm the
   verification mail actually arrives and the link works, THEN invite
   others. Resend's default `@resend.dev` sandbox sender only reaches the
   inbox that owns the Resend account; verify a custom domain at
   resend.com/domains and set `EMAIL_FROM` before sending to strangers.
   Brevo has no sandbox: any address you have verified as a sender can reach
   arbitrary recipients, so test one real signup before opening registration.

To turn the gate off (open signup without email confirmation), set
`REQUIRE_EMAIL_VERIFICATION=false` in the Render environment — never commit
that change in `render.yaml` unless you also drop the mail-backend
requirement with it.

## Bootstrap the first admin

Open the service's **Shell** (paid plans only — on the free plan a one-off job
is not available) and run:

```bash
BOOTSTRAP_EMAIL=you@example.com \
BOOTSTRAP_PASSWORD="use-a-long-password" \
python scripts/bootstrap_admin.py
```

On the free plan there is no shell access, so run the same command against a
local checkout that points at the same database:

```bash
DATABASE_URL=postgresql://postgres.<ref>:<password>@aws-0-<region>.pooler.supabase.com:5432/postgres?sslmode=require \
BOOTSTRAP_EMAIL=you@example.com \
BOOTSTRAP_PASSWORD="use-a-long-password" \
.venv/bin/python scripts/bootstrap_admin.py
```

The script creates the first admin, profile and invite code, then refuses to
run if any user already exists. Sign in and use the Profile page to create
additional invite links.

## Deploy updates & keep-warm

- Every push to the connected branch auto-deploys.
- To stop spin-downs, point a free uptime pinger such as cron-job.org at
  `https://<your-url>/health` every 10 minutes. Watch the **Monthly Free
  Instance Hours** meter in the dashboard: one always-on service is ~744
  h/month against a 750 h allowance.
- Verify after each deploy:

  ```bash
  curl -fsS https://<your-url>/health
  ```

## Frontend

The Docker image builds the React bundle and FastAPI serves it at `/`, so the
Render URL alone is the whole app. There is no separate frontend deployment:
one origin, no CORS or cookie-crossing to configure.
