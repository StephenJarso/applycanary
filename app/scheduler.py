"""Background job scheduler.

Runs in the same process as the web app so `python run.py` is the whole system.

Every job is wrapped in `_guard`, which catches and logs exceptions. An unhandled
error inside an APScheduler job silently kills that job's future runs — which
would look exactly like "the bot stopped finding jobs" with nothing in the logs.

`max_instances=1` and `coalesce=True` mean a slow cycle delays the next run
rather than stacking concurrent ones. Jitter spreads polls so boards are not hit
on a fixed beat.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Coroutine
from typing import Any

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger
from sqlmodel import select

from app.config import get_settings
from app.db import session_scope
from app.models import Job, JobScore, JobStatus, Profile, utcnow

log = logging.getLogger(__name__)

_scheduler: AsyncIOScheduler | None = None


def _guard(name: str, fn: Callable[[], Coroutine[Any, Any, Any]]):  # noqa: ANN202
    """Wrap a job so a failure is logged instead of killing the schedule."""

    async def wrapper() -> None:
        try:
            await fn()
        except Exception:  # noqa: BLE001
            log.exception("scheduled job %r failed", name)

    wrapper.__name__ = f"guarded_{name}"
    return wrapper


# ---------------------------------------------------------------- jobs


async def job_poll_curated() -> None:
    from app.pipeline.ingest import poll_curated

    summary = await poll_curated()
    log.info(
        "poll_curated: %d sources, %d found, %d new, %d dup, %d failed",
        summary.sources_run, summary.found, summary.new,
        summary.duplicates, summary.sources_failed,
    )


async def job_poll_broad() -> None:
    from app.pipeline.ingest import poll_broad

    summary = await poll_broad()
    log.info(
        "poll_broad: %d sources, %d found, %d new, %d dup, %d failed",
        summary.sources_run, summary.found, summary.new,
        summary.duplicates, summary.sources_failed,
    )


async def job_score_new() -> None:
    from app.notify import email as notify
    from app.pipeline.score import score_pending

    settings = get_settings()
    with session_scope() as session:
        count = await score_pending(session, limit=25)
        if not count:
            return
        log.info("score_new: scored %d jobs", count)

        # Alert immediately on exceptional matches; waiting for the daily digest
        # would forfeit the early-application advantage.
        rows = session.exec(
            select(Job, JobScore)
            .join(JobScore, JobScore.job_id == Job.id)
            .where(Job.status == JobStatus.SCORED)
            .where(JobScore.total >= settings.alert_min_score)
            .order_by(JobScore.total.desc())
            .limit(5)
        ).all()
        for job, score in rows:
            await notify.send_alert(job, score)


async def job_prepare_queue() -> None:
    from app.apply.runner import prepare_queue

    with session_scope() as session:
        count = await prepare_queue(session, limit=8)
        if count:
            log.info("prepare_queue: prepared %d applications", count)


async def job_auto_submit() -> None:
    """Submit eligible jobs. A no-op unless ENABLE_AUTO_SUBMIT is true."""
    from app.apply.runner import submit

    settings = get_settings()
    if not settings.enable_auto_submit:
        return

    with session_scope() as session:
        profile = session.exec(select(Profile)).first()
        if profile is None:
            return

        rows = session.exec(
            select(Job, JobScore)
            .join(JobScore, JobScore.job_id == Job.id)
            .where(Job.status == JobStatus.QUEUED)
            .where(JobScore.total >= settings.auto_submit_min_score)
            .order_by(JobScore.total.desc())
            .limit(5)
        ).all()

        for job, _score in rows:
            result = await submit(session, job, profile)
            log.info("auto_submit job %s: ok=%s %s", job.id, result.ok,
                     result.error or result.confirmation)


async def job_refresh_github() -> None:
    from app.pipeline.github import scan

    with session_scope() as session:
        profile = session.exec(select(Profile)).first()
        if profile is None or not profile.github_username:
            return
        evidence = await scan(profile.github_username)
        if evidence.ok:
            profile.github_evidence = evidence.to_dict()
            profile.github_synced_at = utcnow()
            session.add(profile)
            log.info("github: refreshed %d repos", len(evidence.repos))
        else:
            log.warning("github refresh failed: %s", evidence.error)


async def job_digest() -> None:
    from app.notify import email as notify

    with session_scope() as session:
        await notify.send_digest(session)


async def job_expire_stale() -> None:
    """Mark jobs not seen in a fortnight as expired.

    Applied jobs are never expired: that record is the user's application history.
    """
    from datetime import timedelta

    cutoff = utcnow() - timedelta(days=14)
    with session_scope() as session:
        stale = session.exec(
            select(Job)
            .where(Job.last_seen_at < cutoff)
            .where(Job.status.not_in([
                JobStatus.APPLIED, JobStatus.EXPIRED, JobStatus.QUEUED,
            ]))
        ).all()
        for job in stale:
            job.status = JobStatus.EXPIRED
            session.add(job)
        if stale:
            log.info("expired %d stale jobs", len(stale))


# ---------------------------------------------------------------- wiring


def build_scheduler() -> AsyncIOScheduler:
    scheduler = AsyncIOScheduler(
        job_defaults={
            "max_instances": 1,   # never stack runs of the same job
            "coalesce": True,     # collapse missed runs into one
            "misfire_grace_time": 300,
        }
    )

    settings = get_settings()

    specs: list[tuple[str, Any, Callable]] = [
        # Curated boards are the fast-apply edge, so they get the tightest loop.
        (
            "poll_curated",
            IntervalTrigger(minutes=settings.poll_curated_minutes, jitter=60),
            job_poll_curated,
        ),
        (
            "poll_broad",
            IntervalTrigger(minutes=settings.poll_broad_minutes, jitter=300),
            job_poll_broad,
        ),
        (
            "score_new",
            IntervalTrigger(minutes=settings.score_interval_minutes, jitter=20),
            job_score_new,
        ),
        ("prepare_queue", IntervalTrigger(minutes=7, jitter=60), job_prepare_queue),
        ("auto_submit", IntervalTrigger(minutes=10, jitter=60), job_auto_submit),
        ("refresh_github", CronTrigger(hour=3, minute=17), job_refresh_github),
        ("digest", CronTrigger(hour=8, minute=3), job_digest),
        ("expire_stale", CronTrigger(hour=4, minute=23), job_expire_stale),
    ]

    for name, trigger, fn in specs:
        scheduler.add_job(_guard(name, fn), trigger=trigger, id=name, name=name)

    return scheduler


def start() -> AsyncIOScheduler:
    global _scheduler  # noqa: PLW0603
    if _scheduler is not None and _scheduler.running:
        return _scheduler
    _scheduler = build_scheduler()
    _scheduler.start()
    log.info(
        "scheduler started with %d jobs: %s",
        len(_scheduler.get_jobs()),
        ", ".join(j.id for j in _scheduler.get_jobs()),
    )
    return _scheduler


def shutdown() -> None:
    global _scheduler  # noqa: PLW0603
    if _scheduler is not None and _scheduler.running:
        _scheduler.shutdown(wait=False)
        log.info("scheduler stopped")
    _scheduler = None


def get_scheduler() -> AsyncIOScheduler | None:
    return _scheduler
