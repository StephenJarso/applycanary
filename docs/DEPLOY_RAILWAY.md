# Railway deployment

Railway is the recommended hosted target for ApplyCanary. The repository already
contains a multi-stage Dockerfile; Railway detects `railway.json`, builds that
Dockerfile, and runs its `CMD`.

## Create the service

1. Open Railway and create a new project from the GitHub repository
   `StephenJarso/applycanary`.
2. Select the `main` branch and deploy the service.
3. Add a Railway Volume to the service with mount path `/data`.
4. Generate a public domain under the service Networking settings.
5. Confirm the service health check is `/health`.

The volume is required. The SQLite database, uploaded resumes, generated CVs and
cache are all stored under `/data`; without it, redeploys lose application data.

## Required variables

Set these in the Railway service Variables tab:

```text
HOST=0.0.0.0
DATA_DIR=/data
DATABASE_URL=sqlite:////data/applycanary.db
TZ=Africa/Nairobi
SECRET_KEY=<random secret>
ENABLE_SCHEDULER=true
ENABLE_AUTO_SUBMIT=false
```

For personalized AI scoring and tailoring, add one provider key:

```text
GEMINI_API_KEY=<key>
# or ANTHROPIC_API_KEY=<key>
```

Optional variables include `GITHUB_USERNAME`, `GITHUB_TOKEN`, SMTP settings,
and Adzuna credentials. Keep application submission disabled until dry-run
artifacts have been reviewed.

## Bootstrap the first admin

After the first deploy, open a private Railway shell for the service and run:

```bash
BOOTSTRAP_EMAIL=you@example.com \
BOOTSTRAP_PASSWORD="use-a-long-password" \
python scripts/bootstrap_admin.py
```

The script creates the first admin, profile, and invite code, then refuses to
run if any user already exists. Sign in with that account and use the Profile
page to create additional invite links.

## Deploy updates

Railway can auto-deploy new commits from the linked GitHub branch. Manual CLI
deployment is also available after linking the project:

```bash
railway up
```

Verify the service after deployment:

```bash
curl -fsS https://<generated-domain>/health
```

The bootstrap command prints the initial invite code after it creates the admin account.
