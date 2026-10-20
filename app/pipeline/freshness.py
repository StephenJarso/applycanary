"""Posting freshness rules.

The default feed is a job-search feed, not an archive: a role posted six months
ago is usually either filled or buried under newer applicants. Thirty days is the
cutoff, with two deliberate exceptions:

* official careers sites (``source == "careers"``) often keep one evergreen page
  per programme rather than republishing a date;
* a small allowlist of organisations that genuinely reopen the same programmes
  (Igalia's Coding Experience, HENNGE's global internship).

Scoring uses the same rule so stale postings do not consume LLM tokens.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy import func, or_
from sqlalchemy.sql.elements import ColumnElement

from app.models import Job, utcnow

DEFAULT_MAX_AGE_DAYS = 30

RECURRING_COMPANIES = frozenset({
    "igalia",
    "hennge",
    "hennge k.k.",
})

RECURRING_SOURCES = frozenset({
    "careers",
})


def effective_posted_at(job: Job) -> datetime:
    return job.posted_at or job.first_seen_at


def is_recurring(job: Job) -> bool:
    return (
        (job.company or "").strip().lower() in RECURRING_COMPANIES
        or (job.source or "").strip().lower() in RECURRING_SOURCES
    )


def is_fresh(
    job: Job, *, max_age_days: int = DEFAULT_MAX_AGE_DAYS, now: datetime | None = None
) -> bool:
    if is_recurring(job):
        return True
    cutoff = (now or utcnow()) - timedelta(days=max_age_days)
    return effective_posted_at(job) >= cutoff


def fresh_clause(
    *, max_age_days: int = DEFAULT_MAX_AGE_DAYS, now: datetime | None = None
) -> ColumnElement[bool]:
    """SQL predicate matching :func:`is_fresh` without loading every row."""
    cutoff = (now or utcnow()) - timedelta(days=max_age_days)
    return or_(
        func.coalesce(Job.posted_at, Job.first_seen_at) >= cutoff,
        func.lower(Job.company).in_(sorted(RECURRING_COMPANIES)),
        Job.source.in_(sorted(RECURRING_SOURCES)),
    )
