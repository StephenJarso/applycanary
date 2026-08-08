# Deploying to Oracle Cloud Always Free

Oracle's Always Free tier is the only genuinely $0 host that supports what this
app needs: a process that runs continuously and a disk that survives restarts.
Serverless platforms cannot run it — see [Why not Vercel](#why-not-vercel).

Allowance as of June 2026: **2 OCPU / 12 GB RAM** on Ampere A1 (Arm), 200 GB
block storage, no expiry while the account stays active. Oracle halved the Arm
allocation from 4 OCPU / 24 GB without announcement, so treat any guide written
before mid-2026 as stale. Even halved, this is roughly ten times what
ApplyCanary needs.

---

## 1. Create the instance

In the Oracle Cloud console: **Compute → Instances → Create instance**.

| Setting | Value |
|---|---|
| Image | Canonical Ubuntu 24.04 |
| Shape | `VM.Standard.A1.Flex` (Ampere, Arm) |
| OCPUs / memory | 1 OCPU / 6 GB is plenty; up to 2 / 12 stays free |
| Boot volume | 50 GB (well inside the 200 GB allowance) |
| SSH keys | Upload your public key |

**Expect "Out of host capacity."** A1 instances are heavily contended and this
is the single most common obstacle, not a mistake on your part. Retry in a
different availability domain, or at a quieter hour. It usually succeeds within
a day of attempts.

Note the public IP once it boots.

### Arm, not x86

The A1 shape is `aarch64`. Both base images in the Dockerfile — `python:3.12-slim`
and `node:22-slim` — publish arm64 variants, so `docker compose build` works
natively without emulation. If you later add a dependency that ships x86-only
wheels, that is where it will surface.

---

## 2. Prepare the box

```bash
ssh ubuntu@YOUR_IP

sudo apt update && sudo apt upgrade -y
sudo apt install -y docker.io docker-compose-v2 git
sudo usermod -aG docker $USER
```

Log out and back in for the group change to apply, then confirm:

```bash
docker run --rm hello-world
```

### The iptables trap

Oracle's Ubuntu images ship with restrictive `iptables` rules that block traffic
**even when the VCN Security List allows it**. This catches nearly everyone.

You do not need to touch it for this deployment, because nothing is published
publicly — see [Access](#4-access). If you later expose a port and it appears
dead despite correct Security List rules, this is why.

---

## 3. Deploy

```bash
git clone git@github.com:StephenJarso/applycanary.git
cd applycanary

cp .env.example .env
nano .env
```

Set at minimum:

```ini
ANTHROPIC_API_KEY=sk-ant-...
GITHUB_USERNAME=StephenJarso
TZ=Africa/Nairobi
ENABLE_AUTO_SUBMIT=false
```

`TZ` matters. Containers default to UTC and the digest, GitHub refresh and
expiry jobs run on cron triggers in local time — an 08:03 digest would otherwise
arrive at 11:03 in Nairobi.

Then:

```bash
docker compose up -d --build
docker compose logs -f
```

First build takes several minutes: it compiles Python wheels and runs `npm ci`
for the React bundle. Subsequent builds hit the layer cache.

Healthy startup logs look like:

```
database ready at /data/applycanary.db
auto-submit is off — applications wait in the review queue
scheduler started with 8 jobs
```

Verify from the box itself:

```bash
curl -s localhost:8000/health | head -c 200
docker compose ps          # should show (healthy) after ~30s
```

---

## 4. Access

The dashboard **has no authentication**. It serves your resume, salary
expectations, contact details and full application history to anyone who can
reach the port.

`docker-compose.yml` publishes to `127.0.0.1:8000`, so the port is not reachable
from the internet even with a permissive Security List. Reach it over an SSH
tunnel from your laptop:

```bash
ssh -L 8000:127.0.0.1:8000 ubuntu@YOUR_IP
```

Leave that open and browse to **http://127.0.0.1:8000/ui** locally. Traffic goes
over SSH; nothing is exposed.

For always-on access without holding a terminal, [Tailscale](https://tailscale.com)
puts the box on a private network:

```bash
curl -fsSL https://tailscale.com/install.sh | sh
sudo tailscale up
```

Then change the port binding to the Tailscale IP:

```yaml
ports:
  - "100.x.y.z:8000:8000"    # your tailscale0 address, from `tailscale ip -4`
```

**Do not change this to `8000:8000`.** That publishes an unauthenticated
dashboard holding your personal data to the entire internet. If you genuinely
need public access, put a reverse proxy with authentication in front first.

---

## 5. Survive reboots

`restart: unless-stopped` in the compose file handles container restarts, but
Docker itself must start at boot:

```bash
sudo systemctl enable docker
```

Confirm with `sudo reboot`, then after it comes back:

```bash
docker compose ps
```

---

## 6. Back up

`data/` is the only thing not reproducible from the repository: the database,
your uploaded resume, and every generated CV. Postings can be re-fetched;
your application history cannot.

```bash
./scripts/backup.sh
```

Weekly, via the host's crontab (`crontab -e`):

```
17 3 * * 0 cd /home/ubuntu/applycanary && ./scripts/backup.sh >> backups/backup.log 2>&1
```

Copy one down to your laptop periodically — a backup that only exists on the
box it protects is not a backup:

```bash
scp ubuntu@YOUR_IP:applycanary/backups/*.tar.gz .
```

---

## 7. Update

```bash
cd applycanary
git pull
docker compose up -d --build
```

The named volume `applycanary-data` is untouched by rebuilds, so history
survives. `docker compose down -v` **deletes it** — that flag is the one to
avoid.

---

## Operating notes

**Idle reclamation.** Oracle may reclaim Always Free compute that looks idle.
A continuously polling scheduler generates steady CPU and network activity, so
this app is unlikely to trip it, but do not treat the instance as permanent —
keep backups off-box.

**Cost.** Staying inside 2 OCPU / 12 GB and 200 GB storage means $0. Oracle's
Cost Estimator was reportedly showing $0 for configurations that had already
stopped being free around the June 2026 cutover, so verify your tenancy's
actual limits in the console rather than trusting the estimator.

**Watching it.** `GET /health` reports scheduler state and job counts. The
Sources page shows per-connector health — a source returning zero for days
usually means its API changed, not that hiring stopped. Run
`python3 scripts/verify_sources.py` to tell the difference.

---

## Why not Vercel

Recorded because it is the obvious first instinct and it does not work.

- **Cron on Hobby is once per day**, and Vercel rejects a more frequent
  expression at deploy time. This tool exists to poll every 5 minutes.
- **No persistent filesystem.** The SQLite database, uploaded resumes and
  generated CVs would vanish between invocations.
- **10-second function timeout.** A single Greenhouse board with full
  descriptions can take 20 seconds.
- **No long-running process**, so APScheduler has nothing to run inside.

Making it work would mean replacing SQLite with Postgres, replacing APScheduler
with external cron, chunking ingestion into 10-second slices, and moving file
storage to blob — and the free plan would still cap you at one poll per day.

Vercel *is* a good host for the React frontend alone (`frontend/` builds to
static assets), pointed at a backend running elsewhere. For a single-user tool
that adds CORS and auth complexity for little gain, so one box serving both is
simpler.

---

## Not verified

These instructions were written without an Oracle account and without Docker
available, so **no step here has been executed**. The compose and Dockerfile
they rely on are also unbuilt — `docker build` has never run against them.

The parts most likely to need adjustment: the first `docker compose up --build`
(the three-stage build is untested), and Oracle console navigation, which
changes often. The application itself is well tested — 141 passing tests, a
live ingest across ten sources — but its containerisation is not.
