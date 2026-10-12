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

For durable data on the free plan, pair the web service with a managed
database. The repo is already wired for CockroachDB Cloud (free tier):

```bash
scripts/ccloud/provision.sh   # prints a DATABASE_URL — paste it in Render variables
```

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

## Data durability: point at CockroachDB (required on free)

Render's free tier has no persistent disk, so the SQLite file under `/data`
would be wiped on every redeploy and spin-down. The blueprint therefore marks
`DATABASE_URL` as `sync: false` — Render prompts for it at deploy time and you
paste the CockroachDB connection string:

1. Provision the cluster once (needs the `ccloud` CLI and a CockroachDB Cloud
   service account; the free Serverless tier includes nightly backups):

   ```bash
   export CCLOUD_API_KEY=... CCLOUD_API_SECRET=...
   ./scripts/ccloud/provision.sh
   ```

   This creates the `applycanary` cluster + database, enables nightly
   backups, and prints the connection string to use as `DATABASE_URL`:

   ```text
   postgresql://user:password@host:26257/applycanary?sslmode=require
   ```

2. Paste that string when Render prompts for `DATABASE_URL` during blueprint
   apply (or afterwards under **Environment**), then deploy. Tables are
   created automatically on first connect — no migration step is needed.

Because the database lives outside the container, deployments and spin-downs
no longer touch your data.

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
DATABASE_URL=postgresql://user:password@host:26257/applycanary?sslmode=require \
BOOTSTRAP_EMAIL=you@example.com \
BOOTSTRAP_PASSWORD="use-a-long-password" \
.venv/bin/python scripts/bootstrap_admin.py
```

The script creates the first admin, profile and invite code, then refuses to
run if any user already exists. Sign in and use the Profile page to create
additional invite links.

## Deploy updates & keep-warm

- Every push to the connected branch auto-deploys.
- To stop spin-downs, point a free uptime pinger at `https://<your-url>/health`
  every 10 minutes. Watch the **Monthly Free Instance Hours** meter in the
  dashboard: one always-on service is ~744 h/month against a 750 h allowance.
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
