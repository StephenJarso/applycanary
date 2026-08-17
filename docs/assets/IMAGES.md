# ApplyCanary — images for the submission

Everything judges and readers need to *see* the project, in one folder.
All images are 2× resolution (crisp on retina displays and in Devpost galleries).

| File | Size | What it is | Where to use it |
|---|---|---|---|
| `cover.png` | 3200×1800 | Branded hero banner — tagline "Your job search, remembered.", feature cards (Discover / Decide / Interview), CockroachDB + AWS + truthfulness badges | **Devpost cover photo**, repo social preview, demo video thumbnail |
| `architecture.png` | 3200×2080 | Architecture diagram — React dashboard → FastAPI agent on ECS Fargate → AWS services (Bedrock, Polly, Transcribe, S3, CloudWatch) and the CockroachDB Serverless memory layer (VECTOR(1024) + `vec_cosine_ops` index, MCP read-only access, ccloud CLI) | Devpost gallery, README, slide decks |
| `dashboard-jobs.png` | 2880×1800 | Real screenshot of the Jobs dashboard — scored job feed with filters, match percentages, alert threshold banner | Gallery image #1 |
| `job-detail.png` | 2880×1800 | Real screenshot of a job detail page — "Backend Engineer, Developer SDKs (Golang)" at Stripe, match card (keyword / semantic / ATS meters), similar roles from the vector index, AI Interview entry point | Gallery image #2 |
| `interview-studio.png` | 2880×1800 | Real screenshot of the AI Interview Studio — spoken mock interview for a real posting, coach panel, start controls | Gallery image #3 (the wow) |
| `memory.png` | 2880×1800 | Real screenshot of the Memory page — interview stats, improvement trend, "Agent memory — what the coach knows", interview history | Gallery image #4 (the memory layer) |
| `guest-jobs.png` | 2880×1800 | Real screenshot of guest mode — browse and search jobs with no account | Gallery image #5 (open access) |

## Where the images come from

- `cover.png` and `architecture.png` are **hand-designed** (HTML/CSS/SVG sources in
  `src/`) and rendered headlessly — so they stay pixel-perfect and on-brand.
- The five app screenshots are **real captures of the running app** (local
  server, demo account), so what judges see in the images is exactly what the
  product does.

## Regenerating

With the local server running on `:8000` and a demo account configured:

```bash
.venv/bin/python docs/assets/capture.py
```

Env vars to control the capture: `APP_BASE` (default `http://127.0.0.1:8000`),
`CAPTURE_EMAIL` / `CAPTURE_PASSWORD` (the account used for the authenticated
screenshots), and `DEMO_JOB_ID` (which posting the detail/interview shots use).
