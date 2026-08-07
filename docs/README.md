# Documentation

| Document | What it covers |
|---|---|
| [ARCHITECTURE.md](ARCHITECTURE.md) | System design, data flow, and why each layer works the way it does |
| [DATA_MODEL.md](DATA_MODEL.md) | Field-level reference for the eight tables in `app/models.py` |
| [SAFETY.md](SAFETY.md) | Truthfulness verification and the submission gates |
| [OPERATIONS.md](OPERATIONS.md) | Running, tuning, and debugging once it is live |

Setup and configuration reference are in the [README](../README.md).

## Where to start

**Evaluating the design** — read [ARCHITECTURE.md](ARCHITECTURE.md), then
[SAFETY.md](SAFETY.md). The interesting decisions are the two-tier scoring split
and the fact that anti-fabrication is enforced in code rather than requested in a
prompt.

**Running it** — the README covers setup; [OPERATIONS.md](OPERATIONS.md) covers
what to do afterwards. Verify the source connectors before trusting them.

**Modifying it** — [DATA_MODEL.md](DATA_MODEL.md) for the schema. Adding a job
board is one new file in `app/sources/` plus an import.

## Status

Working MVP with known gaps, documented rather than hidden:

- Dependency versions in `requirements.txt` are floors, not pins.
- Source connectors were written against documented API shapes and recorded
  payloads, not verified live. Run `scripts/verify_sources.py` first.
- Only SmartRecruiters supports automated submission; everything else routes to
  the manual review queue.
- Resume file attachment is unimplemented in the SmartRecruiters submitter — see
  [SAFETY.md](SAFETY.md#the-unimplemented-attachment) for why that was the safer
  choice.
- There are no schema migrations.
