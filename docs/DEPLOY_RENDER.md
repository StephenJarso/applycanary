# Render deployment

Render is a good free-tier target for ApplyCanary. The repository already
contains a multi-stage `Dockerfile` and a [`render.yaml`](../render.yaml)
blueprint; Render builds that Dockerfile and runs its `CMD` (`python run.py`) —
the same image used by docker-compose and the Railway setup. No build or port
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
   750 free instance-hours per month, so keeping it warm with an external
   pinger (e.g. cron-job.org hitting `/health`) fits the allowance — but it
   consumes nearly all of it, leaving no room for a second free service.
3. **Outbound SMTP ports (25/465/587) are blocked.** Use an HTTP email API —
   the app already prefers Resend (`RESEND_API_KEY`) over SMTP when set.

For durable data on the free plan, pair the web service with a hosted
Postgres. **Neon** is the recommended pick: free forever, no credit card,
1 GB storage per project, and pgvector support for job embeddings:

1. Sign up at [neon.com/signup](https://neon.com/signup) and create a project.
2. Copy the connection string — it looks like:

   ```text
   postgresql://user:password@ep-xxxx.eu-central-1.aws.neon.tech/neondb?sslmode=require
   ```

   Tip: use the **pooled** connection string (with `-pooler` in the host)
   — Render's ephemeral workers create many short-lived connections.

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
     deploy once the URL is known, then redeploy once).
   - At least one LLM key: `GEMINI_API_KEY` (or `XAI_API_KEY`, `GROQ_API_KEY`,
     `ANTHROPIC_API_KEY`). Without one, scoring falls back to keyword-only.
   - `RESEND_API_KEY` for email (verification, alerts, digest).
4. Click **Apply**. The first build takes a few minutes (frontend `npm build`
   plus the Python venv); subsequent builds cache layers and are faster.

## Data durability: point at Neon (required on free)

Render's free tier has no persistent disk, so the SQLite file under `/data`
would be wiped on every redeploy and spin-down. The blueprint therefore marks
`DATABASE_URL` as `sync: false` — Render prompts for it at deploy time and you
paste the Neon connection string from the signup step above:

1. Paste that string when Render prompts for `DATABASE_URL` during blueprint
   apply (or afterwards under **Environment**), then deploy. Tables are
   created automatically on first connect — no migration step is needed.

2. On Neon's free plan note the two soft limits: scale-to-zero after 5 idle
   minutes (first query after a nap costs ~0.5–1 s wake-up) and 1 GB storage
   per project — plenty for this app's data, but uploaded resume files live
   in `/data` on Render's ephemeral disk and still vanish on redeploy.

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
DATABASE_URL=postgresql://user:password@ep-xxxx-pooler.eu-central-1.aws.neon.tech/neondb?sslmode=require \
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

## Frontend / Vercel wiring

If you keep the React frontend on Vercel with the rewrites in `vercel.json`
(or `frontend/vercel.json`), update both `destination` URLs from the old
Railway domain to your Render URL, e.g.:

```text
https://applycanary.onrender.com/api/:path*
https://applycanary.onrender.com/health
```

Or simpler: skip Vercel entirely — this image serves the built React bundle at
`/` from the same process, so the Render URL alone is the whole app.
