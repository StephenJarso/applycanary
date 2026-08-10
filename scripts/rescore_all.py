"""One-off backfill: rescore every job with the corrected relevance filter.

Existing rows were written when a title mismatch was fatal, so tier 2 never ran
and semantic_score was 0 everywhere. This replays scoring over the whole table.
Safe to re-run: _persist_decision upserts on job_id.
"""

from __future__ import annotations

import asyncio
import logging
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlmodel import Session, select

from app.db import engine
from app.models import Job, JobScore, JobStatus, Profile
from app.pipeline.score import _persist_decision, score_job

logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
log = logging.getLogger("rescore")


async def main() -> None:
    # Free-tier quotas are the binding constraint, not CPU. --only-stale skips
    # jobs already scored by the LLM so an interrupted run can resume without
    # spending the quota twice; --delay paces requests under the rate limit.
    only_stale = "--only-stale" in sys.argv
    delay = 0.0
    for arg in sys.argv:
        if arg.startswith("--delay="):
            delay = float(arg.split("=", 1)[1])

    with Session(engine) as session:
        profile = session.exec(select(Profile)).first()
        if profile is None:
            sys.exit("no profile configured")

        jobs = session.exec(
            select(Job).where(Job.status != JobStatus.SKIPPED)
        ).all()

        if only_stale:
            done = {
                row.job_id
                for row in session.exec(
                    select(JobScore).where(JobScore.model_used != "")
                ).all()
            }
            jobs = [j for j in jobs if j.id not in done]

        total = len(jobs)
        print(
            f"rescoring {total} jobs against profile {profile.id} "
            f"(only_stale={only_stale}, delay={delay}s)",
            flush=True,
        )

        start = time.time()
        tier2_hits = 0
        for i, job in enumerate(jobs, 1):
            try:
                decision = await score_job(session, job, profile)
            except Exception:
                log.exception("job %s failed", job.id)
                continue

            if decision.decided_by == "tier2_llm":
                tier2_hits += 1
                if delay:
                    await asyncio.sleep(delay)
            _persist_decision(session, job, decision)

            if i % 25 == 0 or i == total:
                session.commit()
                rate = i / max(time.time() - start, 1e-6)
                print(
                    f"  {i}/{total} done, {tier2_hits} via LLM, "
                    f"{rate:.1f} jobs/s",
                    flush=True,
                )
        session.commit()
        print(f"finished in {time.time() - start:.0f}s, {tier2_hits} LLM-scored", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
