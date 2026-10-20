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

import asyncio
import logging
from collections.abc import Callable, Coroutine
from datetime import datetime, timedelta
from typing import Any

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.date import DateTrigger
from apscheduler.triggers.interval import IntervalTrigger
from sqlmodel import func, select

from app.config import get_settings
from app.db import session_scope
from app.models import (
    Job,
    JobScore,
    JobStatus,
    Profile,
    PushSubscription,
    User,
    UserJob,
    WatchedOpportunity,
    utcnow,
)
from app.notify.push import push_enabled as push_enabled_check
from app.notify.push import send_push

log = logging.getLogger(__name__)

_scheduler: AsyncIOScheduler | None = None


def _active_profiles(session) -> list[Profile]:  # noqa: ANN001
    """Every active user's profile, for jobs that must run once per user.

    Ingestion is shared — one poll serves everyone. Everything downstream of it
    (scoring, tailoring, submitting, digests) is per-user work and fans out over
    this list.
    """
    return list(
        session.exec(
            select(Profile).join(User, User.id == Profile.user_id).where(User.is_active)
        ).all()
    )


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


async def job_poll_careers() -> None:
    from app.pipeline.ingest import poll_careers

    summary = await poll_careers()
    log.info(
        "poll_careers: %d sources, %d found, %d new, %d dup, %d failed",
        summary.sources_run, summary.found, summary.new,
        summary.duplicates, summary.sources_failed,
    )


async def job_poll_initial() -> None:
    """First sweep shortly after boot, so a fresh deploy polls by default.

    APScheduler interval jobs do not fire until their first interval elapses
    (5–30 minutes here), and a free Render instance that just woke up would sit
    idle until then. Skip when a real ingestion run is already recent so a
    restart loop cannot hammer the boards.
    """
    from app.models import SourceRun, utcnow

    with session_scope() as session:
        latest = session.exec(
            select(func.max(SourceRun.started_at)).where(
                ~SourceRun.source.like("discover:%")
            )
        ).one()
    if latest is not None and (utcnow() - latest).total_seconds() < 600:
        log.info("initial poll skipped: a poll ran %.0fs ago",
                 (utcnow() - latest).total_seconds())
        return

    await asyncio.gather(
        job_poll_curated(), job_poll_careers(), job_poll_broad()
    )


async def job_score_new() -> None:
    from app.notify import email as notify
    from app.pipeline.score import score_pending

    with session_scope() as session:
        for profile in _active_profiles(session):
            count = await score_pending(session, user_id=profile.user_id, limit=25)
            if not count:
                continue
            log.info("score_new: scored %d jobs for user %s", count, profile.user_id)

            # Alert immediately on exceptional matches; waiting for the daily
            # digest would forfeit the early-application advantage. Both the
            # score and the status are this user's own. 0 disables alerts.
            if profile.alert_min_score <= 0:
                continue

            # Only email alerts when the user has their own email address;
            # falling back to the operator's digest_to creates spam for the
            # operator when multiple users are on the instance.
            has_own_email = bool(
                (profile.email and profile.email.strip())
                or (profile.digest_to and profile.digest_to.strip())
            )
            if not has_own_email:
                log.debug(
                    "score_new: skipping alerts for user %s — no email set",
                    profile.user_id,
                )
                continue

            rows = session.exec(
                select(Job, JobScore)
                .join(
                    JobScore,
                    (JobScore.job_id == Job.id)
                    & (JobScore.user_id == profile.user_id),
                )
                .join(
                    UserJob,
                    (UserJob.job_id == Job.id) & (UserJob.user_id == profile.user_id),
                )
                .where(UserJob.status == JobStatus.SCORED)
                .where(JobScore.total >= profile.alert_min_score)
                # Only strong or possible matches — weak/disqualified should
                # never trigger an email alert.
                .where(JobScore.verdict.in_(["strong_match", "possible"]))
                # Keyword-only scores are not evidence of fit: with no resume
                # context they reward generic postings (a finance job once hit
                # 100 on token overlap alone). Only alert on scores an LLM
                # actually assessed, so tier-1 noise never reaches the inbox.
                .where(JobScore.decided_by == "tier2_llm")
                .order_by(JobScore.total.desc())
                .limit(3)
            ).all()

            # Further filter by work-type preference.
            rows = _filter_by_work_type(rows, profile)

            user = session.get(User, profile.user_id)
            for job, score in rows:
                await notify.send_alert(job, score, profile=profile, user=user)


def _filter_by_work_type(rows: list, profile) -> list:  # noqa: ANN001
    """Filter scored job rows by the user's preferred work arrangement.

    ``rows`` is a list of (Job, JobScore) tuples from the scoring query.
    Returns only those matching the profile's ``preferred_work_type`` and
    ``remote_only`` settings.
    """
    wtype = getattr(profile, "preferred_work_type", "any") or "any"
    if wtype == "any" and not getattr(profile, "remote_only", False):
        return rows

    filtered: list = []
    for item in rows:
        # Support both (Job, JobScore) and bare Job rows.
        job = item[0] if isinstance(item, tuple) else item
        is_remote = getattr(job, "is_remote", False)
        if wtype == "remote" and not is_remote:
            continue
        if wtype == "onsite" and is_remote:
            continue
        # hybrid matches everything (we cannot distinguish hybrid from other
        # non-remote postings at the data level, so include it).
        if profile.remote_only and not is_remote:
            continue
        filtered.append(item)
    return filtered


async def job_prepare_queue() -> None:
    from app.apply.runner import prepare_queue

    with session_scope() as session:
        for profile in _active_profiles(session):
            count = await prepare_queue(session, user_id=profile.user_id, limit=8)
            if count:
                log.info(
                    "prepare_queue: prepared %d applications for user %s",
                    count, profile.user_id,
                )


async def job_auto_submit() -> None:
    """Submit eligible jobs for each user who has opted in."""
    from app.apply.runner import submit

    with session_scope() as session:
        for profile in _active_profiles(session):
            # Auto-submit is a per-user preference now, so one user enabling it
            # never causes sends on another user's behalf.
            if not profile.enable_auto_submit:
                continue

            rows = session.exec(
                select(Job, JobScore)
                .join(
                    JobScore,
                    (JobScore.job_id == Job.id)
                    & (JobScore.user_id == profile.user_id),
                )
                .join(
                    UserJob,
                    (UserJob.job_id == Job.id) & (UserJob.user_id == profile.user_id),
                )
                .where(UserJob.status == JobStatus.QUEUED)
                .where(JobScore.total >= profile.auto_submit_min_score)
                .order_by(JobScore.total.desc())
                .limit(5)
            ).all()

            for job, _score in rows:
                result = await submit(
                    session, job, profile, user_id=profile.user_id
                )
                log.info("auto_submit job %s (user %s): ok=%s %s",
                         job.id, profile.user_id, result.ok,
                         result.error or result.confirmation)


async def job_refresh_github() -> None:
    from app.pipeline.github import scan

    with session_scope() as session:
        for profile in _active_profiles(session):
            if not profile.github_username:
                continue
            from app.secrets import decrypt_secret

            evidence = await scan(
                profile.github_username,
                token=decrypt_secret(profile.github_token_encrypted),
            )
            if evidence.ok:
                profile.github_evidence = evidence.to_dict()
                profile.github_synced_at = utcnow()
                session.add(profile)
                log.info(
                    "github: refreshed %d repos for user %s",
                    len(evidence.repos), profile.user_id,
                )
            else:
                log.warning(
                    "github refresh failed for user %s: %s",
                    profile.user_id, evidence.error,
                )


async def job_digest() -> None:
    from app.notify import email as notify

    with session_scope() as session:
        for profile in _active_profiles(session):
            # Only send digests when the user has their own email; the
            # operator's fallback address should not receive other users'
            # job digests.
            has_own_email = bool(
                (profile.email and profile.email.strip())
                or (profile.digest_to and profile.digest_to.strip())
            )
            if not has_own_email:
                log.debug(
                    "digest: skipping user %s — no email configured",
                    profile.user_id,
                )
                continue
            user = session.get(User, profile.user_id)
            await notify.send_digest(
                session, profile=profile, user=user, llm_only=True
            )


async def job_discover_roles() -> None:
    """Fetch postings for users' actual target roles (title/skills/GitHub).

    The broad poll pulls whole boards; this job actively searches for each
    user's roles so a Go developer sees Go roles even when the configured
    boards carry few. Runs rarely because Adzuna's free tier is budgeted.
    """
    from app.pipeline.discover import run_discovery

    summary = await run_discovery()
    log.info(
        "discover_roles: %d sources, %d new, %d dup, %d failed",
        summary.sources_run, summary.new, summary.duplicates, summary.sources_failed,
    )


async def job_embed_new() -> None:
    """Keep the distributed vector index warm: embed new postings.

    Without this, the semantic "similar jobs" and memory-recall features would
    miss every posting ingested since the last explicit backfill. Runs far more
    cheaply than scoring: embedding is one model call per new job, and the
    function is idempotent (existing embeddings are left untouched).
    """
    from app.memory.vectors import backfill_embeddings

    with session_scope() as session:
        count = await backfill_embeddings(session, limit=100)
        if count:
            log.info("embeddings: embedded %d new job(s)", count)


async def job_expire_stale() -> None:
    """Mark postings not seen in a fortnight as expired.

    Expiry is global: the posting itself is gone from every source, which is
    true for all users at once. Users who applied or queued keep their own
    UserJob rows and application history regardless — that record is theirs, and
    it is no longer entangled with the posting's lifecycle.
    """
    from datetime import timedelta

    cutoff = utcnow() - timedelta(days=14)
    with session_scope() as session:
        stale = session.exec(
            select(Job)
            .where(Job.last_seen_at < cutoff)
            .where(Job.expired_at.is_(None))
        ).all()
        for job in stale:
            job.expired_at = utcnow()
            session.add(job)
        if stale:
            log.info("expired %d stale jobs", len(stale))


async def job_refresh_opportunities() -> None:
    """Refresh the public events cache once a day (plus once at startup).

    Listings change rarely, so anything more frequent just adds third-party
    traffic. The endpoint still refreshes lazily after the daily TTL expires,
    which covers instances that stayed up across the scheduled run.
    """
    from app.opportunities import refresh_events

    feed = await refresh_events()
    log.info(
        "opportunities: %d events (live=%s)",
        len(feed.items),
        feed.ok,
    )


async def _send_deadline_pushes() -> None:
    """Fire Web Push notifications for watched opportunities closing in 5 days."""
    from datetime import date, timedelta

    from sqlmodel import Session as SyncSession

    from app.db import engine

    today = date.today()
    target_date = (today + timedelta(days=5)).isoformat()

    sent = 0
    stale_ids: list[int] = []

    with SyncSession(engine) as session:
        watched = session.exec(
            select(WatchedOpportunity).where(
                WatchedOpportunity.deadline == target_date
            )
        ).all()

        for w in watched:
            # Skip if already notified today
            if w.notified_at and w.notified_at.date() >= today:
                continue

            subs = session.exec(
                select(PushSubscription).where(PushSubscription.user_id == w.user_id)
            ).all()

            any_sent = False
            for sub in subs:
                result = send_push(
                    endpoint=sub.endpoint,
                    p256dh=sub.p256dh,
                    auth=sub.auth,
                    title="ApplyCanary — Deadline in 5 days",
                    body=f"{w.opp_title} ({w.opp_org}) closes on {w.deadline}. Don't miss it!",
                    url=w.opp_url,
                )
                if result == "ok":
                    any_sent = True
                elif result == "stale":
                    stale_ids.append(sub.id)

            if any_sent:
                w.notified_at = utcnow()
                session.add(w)
            sent += int(any_sent)

        # Clean up stale subscriptions
        for sid in stale_ids:
            stale = session.get(PushSubscription, sid)
            if stale:
                session.delete(stale)

        session.commit()

    log.info("deadline_push: sent %d push notifications", sent)


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
        # Official careers sites are high-signal but low-churn; 30 minutes is
        # fast enough to beat aggregators without hammering a small site.
        ("poll_careers", IntervalTrigger(minutes=30, jitter=120), job_poll_careers),
        (
            "score_new",
            IntervalTrigger(minutes=settings.score_interval_minutes, jitter=20),
            job_score_new,
        ),
        ("prepare_queue", IntervalTrigger(minutes=7, jitter=60), job_prepare_queue),
        ("auto_submit", IntervalTrigger(minutes=10, jitter=60), job_auto_submit),
        ("refresh_github", CronTrigger(hour=3, minute=17), job_refresh_github),
        # Role discovery is slow and budgeted (Adzuna free tier), so 6-hourly.
        ("discover_roles", CronTrigger(hour="*/6", minute=37), job_discover_roles),
        ("digest", CronTrigger(hour=8, minute=3), job_digest),
        ("expire_stale", CronTrigger(hour=4, minute=23), job_expire_stale),
        ("embed_new", IntervalTrigger(minutes=15, jitter=120), job_embed_new),
        # The public Opportunities page reads a cached TGK event list; keep it
        # warm so page loads never wait on a third-party fetch.
        # Events/programs barely change; startup warms the cache and this daily
        # job is the only scheduled refresh, avoiding needless third-party hits.
        ("refresh_opportunities", CronTrigger(hour=3, minute=7), job_refresh_opportunities),
    ]

    # DateTrigger, not IntervalTrigger: run once ~20s after startup. `replace`
    # keeps a re-started scheduler from raising on the duplicate id.
    scheduler.add_job(
        _guard("poll_initial", job_poll_initial),
        trigger=DateTrigger(run_date=datetime.now() + timedelta(seconds=20)),
        id="poll_initial",
        name="poll_initial",
        replace_existing=True,
    )

    for name, trigger, fn in specs:
        scheduler.add_job(_guard(name, fn), trigger=trigger, id=name, name=name)

    if push_enabled_check():
        scheduler.add_job(
            _guard("deadline_push", _send_deadline_pushes),
            CronTrigger(hour=8, jitter=300),
            id="deadline_push",
            name="deadline_push",
            replace_existing=True,
        )

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
