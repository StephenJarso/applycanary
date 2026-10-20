"""JSON API backing the React dashboard.

Mounted at /api alongside the Jinja routes in `routes.py`, which keep working
unchanged. That means the server-rendered UI stays available as a fallback and
the two can be compared directly while the SPA is built.

Response shapes are declared as Pydantic models rather than returned as loose
dicts, so FastAPI publishes an accurate schema at /docs and the TypeScript
client has something real to mirror.
"""

from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path
from typing import Any, Literal

from fastapi import APIRouter, Body, Depends, HTTPException, Query, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy import and_, or_
from sqlmodel import Session, func, select

from app.config import get_settings
from app.db import get_session
from app.deps import current_user, require_admin, user_job
from app.models import (
    Application,
    Feedback,
    InterviewPrep,
    InterviewSession,
    Job,
    JobAlias,
    JobScore,
    JobStatus,
    Profile,
    ResumeVersion,
    SourceRun,
    User,
    UserJob,
    utcnow,
)

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["api"])


# ---------------------------------------------------------------- schemas


class ScoreOut(BaseModel):
    total: float
    keyword_score: float
    semantic_score: float
    ats_score: float
    verdict: str
    reasoning: str
    decided_by: str
    disqualifier: str
    model_used: str
    matched_keywords: list[str]
    missing_keywords: list[str]
    scored_at: datetime | None = None


class JobOut(BaseModel):
    id: int
    company: str
    title: str
    location: str
    is_remote: bool
    source: str
    status: str
    apply_url: str
    ats_platform: str
    salary_min: int | None = None
    salary_max: int | None = None
    salary_currency: str = ""
    salary_is_estimate: bool = False
    applicants: int | None = None
    benefits: list[str] = Field(default_factory=list)
    employment_type: str = ""
    posted_at: datetime | None = None
    first_seen_at: datetime
    seen_count: int
    # Hours since posting. Precomputed because the whole point of the tool is
    # acting early, and the client should not have to re-derive it per row.
    age_hours: float
    score: ScoreOut | None = None


class JobDetailOut(JobOut):
    description: str = ""
    canonical_url: str = ""
    aliases: list[dict[str, Any]] = Field(default_factory=list)
    application: dict[str, Any] | None = None
    resume_version: dict[str, Any] | None = None
    interview_prep: dict[str, Any] | None = None


class JobListOut(BaseModel):
    jobs: list[JobOut]
    total: int
    counts: dict[str, int]
    sources: list[str]


class ReviewListOut(BaseModel):
    jobs: list[JobOut]
    total: int


class OpportunityOut(BaseModel):
    """One listing on the public Events & paid programs page.

    Mirrors the dicts `app/opportunities.py` builds: live events from the TGK
    calendar plus the curated paid programs and internships. `kind` decides
    whether the card counts down to an event date or to an application
    deadline.
    """

    kind: Literal["Event", "Paid program", "Internship"]
    title: str
    org: str
    when: str
    city: str
    tags: list[str] = Field(default_factory=list)
    source: str
    url: str
    desc: str = ""
    date: str = ""  # ISO event date; "" when only a printed date was available
    opens: str | None = None  # application window, paid programs only
    closes: str | None = None
    steps: list[str] | None = None
    stipend: str | None = None


class OpportunitiesOut(BaseModel):
    items: list[OpportunityOut]
    # False when the live event feed could not be refreshed: the items are the
    # last good list (or programs only), and the UI should say so.
    events_live: bool
    events_updated_at: datetime | None = None


class ProfileOut(BaseModel):
    full_name: str = ""
    email: str = ""
    phone: str = ""
    location: str = ""
    linkedin_url: str = ""
    github_username: str = ""
    portfolio_url: str = ""
    referral_link: str = ""
    min_salary: int | None = None
    salary_currency: str = "USD"
    target_titles: list[str] = Field(default_factory=list)
    target_locations: list[str] = Field(default_factory=list)
    excluded_companies: list[str] = Field(default_factory=list)
    work_authorization: str = ""
    years_experience: int | None = None
    remote_only: bool = False
    has_resume: bool = False
    resume_words: int = 0
    skills: list[str] = Field(default_factory=list)
    github_synced_at: datetime | None = None
    github_repo_count: int = 0
    github_connected: bool = False
    # Email alerts: when a posting scores at/above this, email it immediately.
    alert_min_score: float = 90.0
    # Per-user LLM key
    llm_provider: str = ""
    llm_api_key_masked: str = ""  # masked, never the raw key
    # Job subscription preferences
    preferred_work_type: str = "any"


class ProfileIn(BaseModel):
    full_name: str = ""
    email: str = ""
    phone: str = ""
    location: str = ""
    linkedin_url: str = ""
    github_username: str = ""
    referral_link: str = ""
    portfolio_url: str = ""
    min_salary: int | None = None
    salary_currency: str = "USD"
    target_titles: list[str] = Field(default_factory=list)
    target_locations: list[str] = Field(default_factory=list)
    excluded_companies: list[str] = Field(default_factory=list)
    work_authorization: str = ""
    years_experience: int | None = None
    remote_only: bool = False
    # User-confirmed skills supplement skills detected from the uploaded resume.
    skills: list[str] = Field(default_factory=list)
    # Email alerts: send immediately when a posting scores at/above this (0 = off).
    alert_min_score: float | None = None
    # Per-user LLM key (optional — blank means use server default)
    llm_provider: str = ""
    llm_api_key: str = ""  # sent once, stored, never read back raw
    # GitHub access token: sent once, stored encrypted, never read back.
    github_token: str = ""
    github_disconnect: bool = False
    # Job subscription preferences
    preferred_work_type: str = "any"


class SourceHealthOut(BaseModel):
    source: str
    ok: bool
    runs: int
    failures: int
    found: int
    new_jobs: int
    last_run_at: datetime | None = None
    last_duration_ms: int = 0
    last_error: str = ""


class StatusOut(BaseModel):
    ok: bool
    counts: dict[str, int]
    scheduler_running: bool
    scheduled_jobs: list[str]
    llm_enabled: bool
    poll_running: bool = False
    poll_message: str = ""
    auto_submit: bool
    auto_submit_min_score: int
    daily_apply_cap: int
    warnings: list[str]
    has_profile: bool


class ActionResult(BaseModel):
    ok: bool
    message: str = ""
    detail: dict[str, Any] = Field(default_factory=dict)


class FeedbackIn(BaseModel):
    name: str = ""
    email: str = ""
    category: str = "feedback"
    message: str


class FeedbackUpdate(BaseModel):
    status: str | None = None
    admin_notes: str | None = None



# ---------------------------------------------------------------- helpers


def _score_out(score: JobScore | None) -> ScoreOut | None:
    if score is None:
        return None
    return ScoreOut(
        total=score.total,
        keyword_score=score.keyword_score,
        semantic_score=score.semantic_score,
        ats_score=score.ats_score,
        verdict=score.verdict,
        reasoning=score.reasoning,
        decided_by=score.decided_by,
        disqualifier=score.disqualifier,
        model_used=score.model_used,
        matched_keywords=list(score.matched_keywords or []),
        missing_keywords=list(score.missing_keywords or []),
        scored_at=score.scored_at,
    )


def _job_out(
    job: Job, score: JobScore | None, user_job: UserJob | None = None
) -> JobOut:
    """Serialize a job for one user.

    `user_job` carries that user's workflow status; absent means they have never
    acted on this posting, which reads as NEW. A globally expired posting is
    reported as EXPIRED regardless of any per-user state.
    """
    if job.expired_at is not None:
        status = JobStatus.EXPIRED
    else:
        status = user_job.status if user_job else JobStatus.NEW
    return JobOut(
        id=job.id or 0,
        company=job.company,
        title=job.title,
        location=job.location,
        is_remote=job.is_remote,
        source=job.source,
        status=str(status),
        apply_url=job.apply_url,
        ats_platform=job.ats_platform,
        salary_min=job.salary_min,
        salary_max=job.salary_max,
        salary_currency=job.salary_currency,
        salary_is_estimate=job.salary_is_estimate,
        applicants=job.applicants,
        benefits=list(job.benefits or []),
        employment_type=job.employment_type,
        posted_at=job.posted_at,
        first_seen_at=job.first_seen_at,
        seen_count=job.seen_count,
        age_hours=round(job.age.total_seconds() / 3600, 1),
        score=_score_out(score),
    )


def _profile_or_404(session: Session, user: User) -> Profile:
    profile = session.exec(
        select(Profile).where(Profile.user_id == user.id)
    ).first()
    if profile is None:
        raise HTTPException(404, "no profile configured; save one first")
    return profile


def _apply_text_search(stmt: Any, q: str, model: Any = Job) -> Any:
    """Search over title, company and description that survives real queries.

    A literal substring match on "go developer" finds nothing, because real
    postings say "Senior Golang Engineer" or "Backend Engineer (Golang)". A
    posting matches when any of:
      - every query token appears somewhere (title/company/description),
      - the whole query appears as a phrase in the title/company,
      - the *lead* token appears in the title/company.
    The lead-token rule is what makes "go developer" match "Senior Golang
    Engineer": "go" is inside "golang", and a short title/company match is
    high-signal even when the rest of the query is implied by the role.
    Still plain SQL, still cheap.
    """
    tokens = [t for t in q.split() if len(t) > 1]
    if not tokens:
        return stmt
    phrase = " ".join(tokens)
    return stmt.where(or_(
        and_(*(
            model.title.ilike(f"%{t}%")
            | model.company.ilike(f"%{t}%")
            | model.description.ilike(f"%{t}%")
            for t in tokens
        )),
        model.title.ilike(f"%{phrase}%") | model.company.ilike(f"%{phrase}%"),
        model.title.ilike(f"%{tokens[0]}%") | model.company.ilike(f"%{tokens[0]}%"),
    ))


def _counts(session: Session, user: User) -> dict[str, int]:
    """Status tallies for one user.

    Counts come from UserJob, not Job: a posting's status is per-user now, so a
    global GROUP BY would report everyone's pipeline to whoever asked. Jobs the
    user has never touched have no UserJob row and are reported as NEW.
    """
    rows = session.exec(
        select(UserJob.status, func.count(UserJob.id))
        .where(UserJob.user_id == user.id)
        .group_by(UserJob.status)
    ).all()
    counts = {str(status): n for status, n in rows}
    # Always expose the dashboard's keys so the UI never falls back to made-up
    # placeholder numbers when a user has no rows in a given status.
    counts.setdefault(str(JobStatus.APPLIED), 0)
    counts.setdefault(str(JobStatus.QUEUED), 0)
    counts.setdefault(str(JobStatus.SCORED), 0)
    counts.setdefault(str(JobStatus.REJECTED), 0)
    counts["preps"] = session.exec(
        select(func.count(InterviewPrep.id)).where(InterviewPrep.user_id == user.id)
    ).one()

    live_jobs = session.exec(
        select(func.count(Job.id)).where(Job.expired_at.is_(None))
    ).one()
    tracked = sum(counts.values())
    counts[str(JobStatus.NEW)] = counts.get(str(JobStatus.NEW), 0) + max(
        0, live_jobs - tracked
    )
    counts["total"] = sum(counts.values())
    return counts


# ---------------------------------------------------------------- reads


@router.get("/status", response_model=StatusOut)
def status(
    session: Session = Depends(get_session),
    user: User = Depends(current_user),
) -> StatusOut:
    from app import scheduler as sched
    from app.pipeline.ingest import poll_status

    settings = get_settings()
    scheduler = sched.get_scheduler()
    poll = poll_status()
    profile = session.exec(
        select(Profile).where(Profile.user_id == user.id)
    ).first()
    return StatusOut(
        ok=True,
        counts=_counts(session, user),
        scheduler_running=bool(scheduler and scheduler.running),
        scheduled_jobs=[j.id for j in scheduler.get_jobs()] if scheduler else [],
        llm_enabled=settings.llm_enabled,
        poll_running=bool(poll.get("running")),
        poll_message=str(poll.get("message") or ""),
        # Submission thresholds are per-user preferences now; fall back to the
        # process defaults only when the user has no profile yet.
        auto_submit=profile.enable_auto_submit if profile else False,
        auto_submit_min_score=(
            profile.auto_submit_min_score if profile else settings.auto_submit_min_score
        ),
        daily_apply_cap=profile.daily_apply_cap if profile else settings.daily_apply_cap,
        warnings=settings.startup_warnings(),
        has_profile=profile is not None,
    )


@router.get("/public/jobs")
def public_jobs(
    session: Session = Depends(get_session),
    q: str = "",
    source: str = "",
    remote_only: bool = False,
    sort: Literal["newest", "oldest"] = "newest",
    limit: int = Query(100, ge=1, le=200),
    offset: int = Query(0, ge=0),
) -> dict:
    """Read-only job browsing for guests; no profile or workflow data."""
    stmt = select(Job).where(Job.expired_at.is_(None))
    if source:
        stmt = stmt.where(Job.source == source)
    if remote_only:
        stmt = stmt.where(Job.is_remote == True)  # noqa: E712 - SQL, not Python
    if q:
        stmt = _apply_text_search(stmt, q)
    stmt = stmt.order_by(Job.first_seen_at.desc() if sort == "newest" else Job.first_seen_at.asc())
    rows = session.exec(stmt.offset(offset).limit(limit)).all()
    sources = sorted(session.exec(select(Job.source).distinct()).all())
    return {
        "jobs": [{
            "id": job.id, "company": job.company, "title": job.title,
            "location": job.location, "is_remote": job.is_remote,
            "source": job.source, "apply_url": job.apply_url,
            "salary_min": job.salary_min, "salary_max": job.salary_max,
            "salary_currency": job.salary_currency,
            "salary_is_estimate": job.salary_is_estimate,
            "posted_at": job.posted_at, "first_seen_at": job.first_seen_at,
        } for job in rows], "total": len(rows), "sources": sources,
    }

@router.get("/public/opportunities", response_model=OpportunitiesOut)
async def public_opportunities() -> OpportunitiesOut:
    """Live events plus curated programs and internships for the public page.

    Public like /public/jobs: the page is linked from the landing page and
    carries no user data, so it must answer before anyone signs in. The event
    feed is cached upstream in `app.opportunities` and degrades to the last
    good list rather than failing the request.
    """
    from app.opportunities import get_opportunities

    return OpportunitiesOut(**await get_opportunities())


@router.get("/public/internships", response_model=OpportunitiesOut)
def public_internships() -> OpportunitiesOut:
    """Curated internship placements, public like the events page.

    Kept separate from ``/public/opportunities`` so the page can offer a
    dedicated Internships tab without every event request carrying the cards.
    """
    from app.opportunities import get_internships

    return OpportunitiesOut(**get_internships())


@router.get("/jobs", response_model=JobListOut)
def list_jobs(
    session: Session = Depends(get_session),
    user: User = Depends(current_user),
    q: str = "",
    status: str = "",
    source: str = "",
    min_score: int = Query(0, ge=0, le=100),
    remote_only: bool = False,
    prep_only: bool = False,
    include_all: bool = False,
    posted_within: int = Query(30, ge=0, le=3650),
    sort: Literal["score", "newest", "oldest"] = "score",
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
) -> JobListOut:
    # Both joins are per-user: an outer join on UserJob so postings this user has
    # never seen still appear (as NEW), and on JobScore so another user's score
    # never leaks into this list.
    stmt = (
        select(Job, JobScore, UserJob)
        .join(
            JobScore,
            (JobScore.job_id == Job.id) & (JobScore.user_id == user.id),
            isouter=True,
        )
        .join(
            UserJob,
            (UserJob.job_id == Job.id) & (UserJob.user_id == user.id),
            isouter=True,
        )
        .where(Job.expired_at.is_(None))
    )
    profile = session.exec(select(Profile).where(Profile.user_id == user.id)).first()
    has_profile_evidence = bool(profile and (profile.base_resume_text or profile.skills))
    if status == "rejected":
        stmt = stmt.where(UserJob.status == JobStatus.REJECTED)
    elif status:
        stmt = stmt.where(UserJob.status == status)
    elif include_all:
        # The "View All" feed: every live posting, including ones the scorer
        # has not reached and ones tier-1 rejected for this user.
        pass
    else:
        # Default to relevant jobs only; REJECTED jobs are tier-1 disqualifications
        # (irrelevant titles/seniority etc.) and are hidden unless explicitly
        # requested. A NULL status means "never seen", which is not rejected.
        stmt = stmt.where(
            (UserJob.status.is_(None)) | (UserJob.status != JobStatus.REJECTED)
        )
        if has_profile_evidence:
            # Unscored postings are intentionally hidden from the default view;
            # they have not yet been judged against this user's profile.
            # An explicit search is different: it should find matches, not only
            # postings the scheduler has already got to.
            if not q:
                stmt = stmt.where(JobScore.id.is_not(None))
    if source:
        stmt = stmt.where(Job.source == source)
    if remote_only:
        stmt = stmt.where(Job.is_remote == True)  # noqa: E712 - SQL, not Python
    if prep_only:
        stmt = stmt.where(
            Job.id.in_(
                select(InterviewPrep.job_id).where(InterviewPrep.user_id == user.id)
            )
        )
    if posted_within:
        # Fall back to first_seen for connectors that do not publish a date.
        # Official careers pages and recurring programmes stay visible even when
        # their printed date is old; see app.pipeline.freshness.
        from app.pipeline.freshness import fresh_clause

        stmt = stmt.where(fresh_clause(max_age_days=posted_within))
    if q:
        stmt = _apply_text_search(stmt, q)
    if min_score:
        stmt = stmt.where(JobScore.total >= min_score)

    if sort == "newest":
        stmt = stmt.order_by(Job.first_seen_at.desc())
    elif sort == "oldest":
        stmt = stmt.order_by(Job.first_seen_at.asc())
    else:
        stmt = stmt.order_by(JobScore.total.desc().nullslast(), Job.first_seen_at.desc())

    # Total count before LIMIT/OFFSET — the dashboard needs this for pagination.
    from sqlalchemy import func as sa_func

    count_stmt = (
        select(sa_func.count())
        .select_from(stmt.subquery())
    )
    total = session.exec(count_stmt).one()

    rows = session.exec(stmt.offset(offset).limit(limit)).all()
    from app.sources import all_sources

    distinct_db_sources = set(session.exec(select(Job.source).distinct()).all())
    all_configured = set(all_sources().keys())
    sources = sorted([s for s in (distinct_db_sources | all_configured) if s])

    return JobListOut(
        jobs=[_job_out(job, score, uj) for job, score, uj in rows],
        total=total,
        counts=_counts(session, user),
        sources=sources,
    )


@router.get("/jobs/{job_id}", response_model=JobDetailOut)
def job_detail(
    job_id: int,
    session: Session = Depends(get_session),
    user: User = Depends(current_user),
) -> JobDetailOut:
    job = session.get(Job, job_id)
    if job is None:
        raise HTTPException(404, "job not found")

    # Every per-user row is filtered by user_id as well as job_id. Without that
    # second predicate this endpoint would serve whichever user's score, résumé
    # and cover letter happened to be attached to the posting.
    score = session.exec(
        select(JobScore).where(
            JobScore.job_id == job_id, JobScore.user_id == user.id
        )
    ).first()
    app_row = session.exec(
        select(Application).where(
            Application.job_id == job_id, Application.user_id == user.id
        )
    ).first()
    version = (
        session.get(ResumeVersion, app_row.resume_version_id)
        if app_row and app_row.resume_version_id
        else None
    )
    prep = session.exec(
        select(InterviewPrep).where(
            InterviewPrep.job_id == job_id, InterviewPrep.user_id == user.id
        )
    ).first()
    aliases = session.exec(select(JobAlias).where(JobAlias.job_id == job_id)).all()
    state = session.exec(
        select(UserJob).where(
            UserJob.job_id == job_id, UserJob.user_id == user.id
        )
    ).first()

    base = _job_out(job, score, state)
    return JobDetailOut(
        **base.model_dump(),
        description=job.description,
        canonical_url=job.canonical_url,
        aliases=[
            {"source": a.source, "matched_by": a.matched_by,
             "match_score": a.match_score, "apply_url": a.apply_url}
            for a in aliases
        ],
        application=(
            {
                "method": str(app_row.method),
                "queued_at": app_row.queued_at,
                "submitted_at": app_row.submitted_at,
                "confirmation": app_row.confirmation,
                "error": app_row.error,
                "attempts": app_row.attempts,
                "cover_letter": app_row.cover_letter,
                "form_answers": app_row.form_answers or {},
                "outcome": app_row.outcome,
            }
            if app_row else None
        ),
        resume_version=(
            {
                "id": version.id,
                "text": version.text,
                "text_html": version.text_html or version.text,
                "original_text": version.original_text,
                "original_html": version.original_html or version.original_text,
                "format_preserved": version.format_preserved,
                "template_note": version.template_note,
                "diff_summary": version.diff_summary,
                "ats_score_before": version.ats_score_before,
                "ats_score_after": version.ats_score_after,
                "keywords_added": list(version.keywords_added or []),
                "truthcheck_passed": version.truthcheck_passed,
                "truthcheck_notes": list(version.truthcheck_notes or []),
                "unverifiable_claims": list(version.unverifiable_claims or []),
                "docx_path": version.docx_path,
                "pdf_path": version.pdf_path,
            }
            if version else None
        ),
        interview_prep=(
            {
                "technical_questions": prep.technical_questions or [],
                "behavioural_questions": prep.behavioural_questions or [],
                "questions_to_ask": list(prep.questions_to_ask or []),
                "company_notes": prep.company_notes,
                "skill_gaps": list(prep.skill_gaps or []),
                "speech_interview": prep.speech_interview or [],
                "technical_interview": prep.technical_interview or [],
            }
            if prep else None
        ),
    )


@router.get("/review", response_model=ReviewListOut)
def review_queue(
    session: Session = Depends(get_session),
    user: User = Depends(current_user),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
) -> ReviewListOut:
    """Applications prepared but not yet submitted."""
    stmt = (
        select(Job, JobScore, UserJob)
        .join(
            Application,
            (Application.job_id == Job.id) & (Application.user_id == user.id),
        )
        .join(
            JobScore,
            (JobScore.job_id == Job.id) & (JobScore.user_id == user.id),
            isouter=True,
        )
        .join(
            UserJob,
            (UserJob.job_id == Job.id) & (UserJob.user_id == user.id),
            isouter=True,
        )
        .where(Application.submitted_at.is_(None))
        .order_by(JobScore.total.desc().nullslast())
    )
    from sqlalchemy import func as sa_func

    total = session.exec(select(sa_func.count()).select_from(stmt.subquery())).one()
    rows = session.exec(stmt.offset(offset).limit(limit)).all()
    return ReviewListOut(
        jobs=[_job_out(job, score, uj) for job, score, uj in rows],
        total=total,
    )


@router.get("/applications", response_model=list[JobOut])
def applications(
    session: Session = Depends(get_session),
    user: User = Depends(current_user),
) -> list[JobOut]:
    rows = session.exec(
        select(Job, JobScore, UserJob)
        .join(
            Application,
            (Application.job_id == Job.id) & (Application.user_id == user.id),
        )
        .join(
            JobScore,
            (JobScore.job_id == Job.id) & (JobScore.user_id == user.id),
            isouter=True,
        )
        .join(
            UserJob,
            (UserJob.job_id == Job.id) & (UserJob.user_id == user.id),
            isouter=True,
        )
        .where(Application.submitted_at.is_not(None))
        .order_by(Application.submitted_at.desc())
    ).all()
    return [_job_out(job, score, uj) for job, score, uj in rows]


@router.get("/sources", response_model=list[SourceHealthOut])
def sources(session: Session = Depends(get_session)) -> list[SourceHealthOut]:
    from app.sources import all_sources

    by_source: dict[str, SourceHealthOut] = {
        name: SourceHealthOut(
            source=name, ok=True, runs=0, failures=0, found=0, new_jobs=0,
            last_run_at=None, last_duration_ms=0, last_error="",
        )
        for name in all_sources()
    }

    runs = session.exec(
        select(SourceRun).order_by(SourceRun.started_at.desc()).limit(200)
    ).all()

    for run in runs:
        entry = by_source.get(run.source)
        if entry is None:
            entry = SourceHealthOut(
                source=run.source, ok=run.ok, runs=0, failures=0, found=0, new_jobs=0,
                last_run_at=run.started_at, last_duration_ms=run.duration_ms,
                last_error=run.error,
            )
            by_source[run.source] = entry
        elif entry.runs == 0:
            entry.ok = run.ok
            entry.last_run_at = run.started_at
            entry.last_duration_ms = run.duration_ms
            entry.last_error = run.error

        entry.runs += 1
        entry.found += run.found
        entry.new_jobs += run.new_jobs
        if not run.ok:
            entry.failures += 1

    return sorted(by_source.values(), key=lambda e: e.source)


@router.get("/profile", response_model=ProfileOut)
def get_profile(
    session: Session = Depends(get_session),
    user: User = Depends(current_user),
) -> ProfileOut:

    profile = session.exec(
        select(Profile).where(Profile.user_id == user.id)
    ).first()
    if profile is None:
        return ProfileOut()
    evidence = profile.github_evidence or {}
    # Admins always use the server's LLM pipeline. Never surface a stored
    # personal key for an admin account (the UI hides the card as well).
    key = "" if user.is_admin else (profile.llm_api_key or "")
    masked = (key[:4] + "••••••••" + key[-4:]) if len(key) > 8 else ("••••••••" if key else "")
    return ProfileOut(
        full_name=profile.full_name,
        email=profile.email,
        phone=profile.phone,
        location=profile.location,
        linkedin_url=profile.linkedin_url,
        github_username=profile.github_username,
        portfolio_url=profile.portfolio_url,
        referral_link=profile.referral_link,
        min_salary=profile.min_salary,
        salary_currency=profile.salary_currency,
        target_titles=list(profile.target_titles or []),
        target_locations=list(profile.target_locations or []),
        excluded_companies=list(profile.excluded_companies or []),
        work_authorization=profile.work_authorization,
        years_experience=profile.years_experience,
        remote_only=profile.remote_only,
        has_resume=bool(profile.base_resume_text),
        resume_words=len((profile.base_resume_text or "").split()),
        skills=list(profile.skills or []),
        github_synced_at=profile.github_synced_at,
        github_repo_count=len(evidence.get("repos") or []),
        github_connected=bool(profile.github_token_encrypted),
        alert_min_score=profile.alert_min_score,
        llm_provider="" if user.is_admin else profile.llm_provider,
        llm_api_key_masked=masked,
        preferred_work_type=profile.preferred_work_type,
    )


# ---------------------------------------------------------------- writes


@router.put("/profile", response_model=ProfileOut)
def save_profile(
    payload: ProfileIn,
    session: Session = Depends(get_session),
    user: User = Depends(current_user),
) -> ProfileOut:
    from app.pipeline.keywords import extract_skills

    profile = session.exec(
        select(Profile).where(Profile.user_id == user.id)
    ).first() or Profile(user_id=user.id)
    # Partial update: only fields the client actually sent are applied. A
    # payload that omits target_titles means "leave my titles alone", not
    # "wipe them" — the old behaviour reset every omitted field to its model
    # default, which silently destroyed data when a caller sent a subset.
    if payload.github_disconnect:
        profile.github_token_encrypted = ""
    elif payload.github_token:
        from app.secrets import encrypt_secret

        profile.github_token_encrypted = encrypt_secret(payload.github_token)

    for field, value in payload.model_dump(exclude_unset=True).items():
        # None means "leave unchanged" for the optional alert threshold, so an
        # old client that omits it cannot null out the stored preference.
        if field == "alert_min_score" and value is None:
            continue
        if field in ("github_token", "github_disconnect"):
            continue
        # llm_api_key: only overwrite when the user actually sent a new key;
        # an empty string means "I forgot to fill this in", not "clear my key".
        if field == "llm_api_key":
            if value:
                profile.llm_api_key = str(value)
            continue
        setattr(profile, field, value)
    if "skills" in payload.model_fields_set:
        # Store canonical skill names so variants such as "Golang" and "go" score
        # identically and display consistently.
        profile.skills = sorted(extract_skills(", ".join(payload.skills)))
    if "github_username" in payload.model_fields_set:
        profile.github_username = profile.github_username.strip().removeprefix("@")
    if user.is_admin:
        # Clear stale personal credentials: admins are funded by the operator.
        profile.llm_provider = ""
        profile.llm_api_key = ""
    profile.updated_at = utcnow()
    session.add(profile)
    session.commit()
    return get_profile(session, user)


@router.post("/profile/resume", response_model=ActionResult)
async def upload_resume(
    request: Request,
    session: Session = Depends(get_session),
    user: User = Depends(current_user),
) -> ActionResult:
    from pathlib import Path

    from app.pipeline.keywords import extract_skills
    from app.resume.parse import SUPPORTED, parse_resume

    form = await request.form()
    upload = form.get("resume")
    if upload is None or not getattr(upload, "filename", None) or not hasattr(upload, "read"):
        raise HTTPException(400, "no file uploaded under field 'resume'")

    suffix = Path(upload.filename).suffix.lower()
    if suffix not in SUPPORTED:
        raise HTTPException(400, f"unsupported type {suffix}; use {sorted(SUPPORTED)}")

    settings = get_settings()
    settings.ensure_dirs()
    # Namespaced per user: a shared "base.pdf" meant each upload silently
    # overwrote the previous user's résumé on disk.
    user_dir = settings.resume_dir / f"user_{user.id}"
    user_dir.mkdir(parents=True, exist_ok=True)
    dest = user_dir / f"base{suffix}"
    dest.write_bytes(await upload.read())

    try:
        parsed = parse_resume(dest)
    except Exception as exc:  # noqa: BLE001
        log.exception("resume parse failed")
        raise HTTPException(400, f"could not parse resume: {exc}") from exc

    profile = session.exec(
        select(Profile).where(Profile.user_id == user.id)
    ).first() or Profile(user_id=user.id)
    profile.base_resume_path = str(dest)
    profile.base_resume_text = parsed.text
    profile.resume_meta = {
        **parsed.template,
        "file_type": parsed.file_type,
        "pages": parsed.pages,
    }
    profile.skills = sorted(extract_skills(parsed.text) | set(profile.skills or []))
    if not profile.email and parsed.emails:
        profile.email = parsed.emails[0]
    if not profile.phone and parsed.phones:
        profile.phone = parsed.phones[0]
    session.add(profile)
    session.commit()

    return ActionResult(
        ok=True,
        message=f"Parsed {parsed.word_count} words, found {len(profile.skills)} skills.",
        detail={"words": parsed.word_count, "skills": profile.skills},
    )


@router.get("/profile/ats", response_model=dict)
def profile_ats(
    session: Session = Depends(get_session),
    user: User = Depends(current_user),
) -> dict:
    """Structural ATS report for the stored resume, independent of any job."""
    from app.pipeline.ats_rules import evaluate

    profile = _profile_or_404(session, user)
    if not profile.base_resume_text:
        raise HTTPException(404, "no resume uploaded")
    return evaluate(profile.base_resume_text).as_dict()


@router.get("/resume-versions/{version_id}/download/{fmt}")
def download_resume_version(
    version_id: int,
    fmt: Literal["docx", "pdf", "txt"],
    session: Session = Depends(get_session),
    user: User = Depends(current_user),
) -> FileResponse:
    """Serve a tailored resume artifact, rendering it on demand if absent.

    Artifacts are regenerated rather than 404'd when the stored path is missing:
    the row holds the resume *text*, so a file cleared by a deploy or a wiped
    data dir is reproducible. Only paths inside the configured artifact dir are
    served, so a tampered DB row cannot turn this into an arbitrary file read.
    """
    version = session.get(ResumeVersion, version_id)
    if version is None:
        raise HTTPException(404, "resume version not found")
    # Ownership check. version_id comes straight from the URL, so without this a
    # user could walk the id space and download everyone else's résumé. 404
    # rather than 403: whether a given id exists is itself not their business.
    if version.user_id is not None and version.user_id != user.id:
        raise HTTPException(404, "resume version not found")
    if not (version.text or "").strip():
        raise HTTPException(409, "this resume version has no text to render")

    job = session.get(Job, version.job_id)
    profile = session.exec(
        select(Profile).where(Profile.user_id == user.id)
    ).first()
    settings = get_settings()

    if fmt == "txt":
        path = settings.artifact_dir / f"resume_{version_id}.txt"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(version.text, encoding="utf-8")
    else:
        stored = Path(version.docx_path if fmt == "docx" else version.pdf_path or "")
        path = stored if stored.name and stored.is_file() else Path()
        if not path:
            from app.resume.render import render_for_job

            docx_path, pdf_path, preserved, note = render_for_job(
                version.text,
                company=job.company if job else "company",
                title=job.title if job else "role",
                full_name=(profile.full_name if profile else "") or "",
                profile=profile,
            )
            version.docx_path = str(docx_path)
            version.pdf_path = str(pdf_path) if str(pdf_path) != "." else ""
            version.format_preserved = preserved
            version.template_note = note
            session.add(version)
            session.commit()
            path = docx_path if fmt == "docx" else pdf_path
        if not path or not path.is_file():
            raise HTTPException(
                503, f"could not render {fmt} for this resume version"
            )

    resolved = path.resolve()
    if not resolved.is_relative_to(settings.artifact_dir.resolve()):
        log.error("refusing to serve %s: outside artifact dir", resolved)
        raise HTTPException(403, "artifact path is outside the artifact directory")

    stem = _download_stem(job, profile)
    return FileResponse(
        resolved,
        media_type=_MEDIA_TYPES[fmt],
        filename=f"{stem}.{fmt}",
    )


_MEDIA_TYPES = {
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "pdf": "application/pdf",
    "txt": "text/plain; charset=utf-8",
}


def _download_stem(job: Job | None, profile: Profile | None) -> str:
    from app.resume.render import slugify

    parts = [
        slugify(((profile.full_name if profile else "") or "resume"), limit=24),
        slugify(job.company if job else "", limit=24),
        slugify(job.title if job else "", limit=32),
    ]
    return "_".join(p for p in parts if p) or "resume"


@router.post("/jobs/{job_id}/tailor", response_model=ActionResult)
async def tailor(
    job_id: int,
    session: Session = Depends(get_session),
    user: User = Depends(current_user),
) -> ActionResult:
    from app.apply.runner import prepare

    job = session.get(Job, job_id)
    if job is None:
        raise HTTPException(404, "job not found")
    profile = _profile_or_404(session, user)
    try:
        app_row = await prepare(session, job, profile, user_id=user.id)
    except Exception as exc:  # noqa: BLE001
        log.exception("tailoring failed for job %s", job_id)
        raise HTTPException(500, str(exc)) from exc
    return ActionResult(
        ok=True, message="Application prepared.",
        detail={"resume_version_id": app_row.resume_version_id},
    )


@router.post("/jobs/{job_id}/submit", response_model=ActionResult)
async def submit(
    job_id: int,
    force: bool = False,
    session: Session = Depends(get_session),
    user: User = Depends(current_user),
) -> ActionResult:
    from app.apply.runner import submit as do_submit

    job = session.get(Job, job_id)
    if job is None:
        raise HTTPException(404, "job not found")
    profile = _profile_or_404(session, user)
    result = await do_submit(session, job, profile, force=force, user_id=user.id)
    return ActionResult(
        ok=result.ok,
        message=result.confirmation or result.error,
        detail={"method": str(result.method), "dry_run": result.dry_run},
    )


@router.post("/jobs/{job_id}/skip", response_model=ActionResult)
def skip(
    job_id: int,
    session: Session = Depends(get_session),
    user: User = Depends(current_user),
) -> ActionResult:
    job = session.get(Job, job_id)
    if job is None:
        raise HTTPException(404, "job not found")
    state = user_job(session, user.id, job_id)
    state.status = JobStatus.SKIPPED
    session.add(state)
    session.commit()
    return ActionResult(ok=True, message="Skipped.")


@router.post("/jobs/{job_id}/prep", response_model=ActionResult)
async def make_prep(
    job_id: int,
    session: Session = Depends(get_session),
    user: User = Depends(current_user),
) -> ActionResult:
    from app.pipeline.interview import prep_for_job

    job = session.get(Job, job_id)
    if job is None:
        raise HTTPException(404, "job not found")
    profile = _profile_or_404(session, user)
    try:
        await prep_for_job(session, job, profile, user_id=user.id)
    except Exception as exc:  # noqa: BLE001
        log.exception("interview prep failed for job %s", job_id)
        raise HTTPException(500, str(exc)) from exc
    return ActionResult(ok=True, message="Interview prep generated.")


@router.post("/actions/poll", response_model=ActionResult)
async def trigger_poll(
    user: User = Depends(current_user),
) -> ActionResult:
    from app.pipeline.ingest import start_full_poll

    # A full sweep can exceed the platform request timeout, which the browser
    # reports as the server being unreachable. Start it in the background and
    # let the UI follow progress through /status instead of holding the request.
    if not start_full_poll(user.id):
        return ActionResult(
            ok=True,
            message="A poll is already running — new postings will appear shortly.",
            detail={"started": False},
        )
    return ActionResult(
        ok=True,
        message="Poll started — new postings will appear shortly.",
        detail={"started": True},
    )


@router.post("/actions/score", response_model=ActionResult)
async def trigger_score(
    session: Session = Depends(get_session),
    user: User = Depends(current_user),
) -> ActionResult:
    from app.pipeline.score import score_pending

    count = await score_pending(session, user_id=user.id, limit=50)
    return ActionResult(ok=True, message=f"Scored {count} jobs.", detail={"scored": count})


@router.post("/actions/discover", response_model=ActionResult)
async def trigger_discover(
    session: Session = Depends(get_session),
    user: User = Depends(current_user),
) -> ActionResult:
    """Run role-driven discovery for the requesting user, right now.

    Builds queries from the profile's target titles, skills and GitHub evidence,
    fetches matching postings (Adzuna + web search), and scores them. This is
    the manual "go find my roles" button behind the scheduled 6-hourly job.
    """
    from app.pipeline.discover import discover_for_user

    result = await discover_for_user(user.id)
    return ActionResult(
        ok=result["ok"],
        message=result.get("message", ""),
        detail=result.get("detail", {}),
    )


@router.post("/actions/github", response_model=ActionResult)
async def trigger_github(
    session: Session = Depends(get_session),
    user: User = Depends(current_user),
) -> ActionResult:
    from app.pipeline.github import scan

    profile = _profile_or_404(session, user)
    if not profile.github_username:
        raise HTTPException(400, "no GitHub username on the profile")
    from app.secrets import decrypt_secret

    evidence = await scan(
        profile.github_username,
        token=decrypt_secret(profile.github_token_encrypted),
    )
    if not evidence.ok:
        raise HTTPException(502, evidence.error or "GitHub scan failed")
    profile.github_evidence = evidence.to_dict()
    profile.github_synced_at = utcnow()
    session.add(profile)
    session.commit()
    return ActionResult(
        ok=True,
        message=f"Scanned {len(evidence.repos)} repositories.",
        detail={"repos": len(evidence.repos), "skills": evidence.skills},
    )


# ---------------------------------------------------------------- feedback & admin


@router.post("/public/feedback")
def submit_feedback(
    payload: FeedbackIn,
    request: Request,
    session: Session = Depends(get_session),
) -> dict:
    """Allow anyone (logged-in user or guest visitor) to submit feedback or contact the admin."""
    msg = payload.message.strip()
    if not msg:
        raise HTTPException(400, "Message cannot be empty")

    user_id = getattr(request.state, "user_id", None)
    sender_email = payload.email.strip()
    sender_name = payload.name.strip()

    if user_id:
        user_row = session.get(User, user_id)
        if user_row:
            if not sender_email:
                sender_email = user_row.email
            if not sender_name:
                sender_name = user_row.email.split("@")[0]

    fb = Feedback(
        user_id=user_id,
        name=sender_name or "Guest",
        email=sender_email,
        category=payload.category.strip() or "feedback",
        message=msg,
        status="new",
    )
    session.add(fb)
    session.commit()
    return {"ok": True, "message": "Thank you! Your feedback has been received."}


@router.get("/admin/feedback")
def list_feedback(
    status: str = "all",
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    user: User = Depends(require_admin),
    session: Session = Depends(get_session),
) -> dict:
    """List user feedback and contact submissions (admin only)."""
    stmt = select(Feedback)
    if status != "all":
        stmt = stmt.where(Feedback.status == status)

    total = session.exec(select(func.count(Feedback.id))).one()
    new_count = session.exec(select(func.count(Feedback.id)).where(Feedback.status == "new")).one()
    items = session.exec(stmt.order_by(Feedback.created_at.desc()).offset(offset).limit(limit)).all()

    return {
        "items": [
            {
                "id": it.id,
                "user_id": it.user_id,
                "name": it.name,
                "email": it.email,
                "category": it.category,
                "message": it.message,
                "status": it.status,
                "admin_notes": it.admin_notes,
                "created_at": it.created_at.isoformat(),
            }
            for it in items
        ],
        "total": total,
        "new_count": new_count,
    }


@router.patch("/admin/feedback/{feedback_id}")
def update_feedback(
    feedback_id: int,
    payload: FeedbackUpdate,
    user: User = Depends(require_admin),
    session: Session = Depends(get_session),
) -> dict:
    """Update feedback status or admin notes."""
    fb = session.get(Feedback, feedback_id)
    if not fb:
        raise HTTPException(404, "Feedback entry not found")
    if payload.status is not None:
        fb.status = payload.status
    if payload.admin_notes is not None:
        fb.admin_notes = payload.admin_notes
    session.add(fb)
    session.commit()
    return {"ok": True, "message": "Feedback updated"}


@router.delete("/admin/feedback/{feedback_id}")
def delete_feedback(
    feedback_id: int,
    user: User = Depends(require_admin),
    session: Session = Depends(get_session),
) -> dict:
    """Delete a feedback entry."""
    fb = session.get(Feedback, feedback_id)
    if not fb:
        raise HTTPException(404, "Feedback entry not found")
    session.delete(fb)
    session.commit()
    return {"ok": True, "message": "Feedback deleted"}


@router.get("/admin/system-status")
def admin_system_status(
    user: User = Depends(require_admin),
    session: Session = Depends(get_session),
) -> dict:
    """Live telemetry for database, scheduler, LLM providers, scrapers and users."""
    import time

    from app import scheduler as sched
    from app.config import get_settings
    from app.db import is_cockroach, vector_backend
    from app.llm.client import get_llm

    settings = get_settings()
    llm = get_llm()

    # User metrics
    users_total = session.exec(select(func.count(User.id))).one()
    users_active = session.exec(select(func.count(User.id)).where(User.is_active.is_(True))).one()
    users_verified = session.exec(select(func.count(User.id)).where(User.email_verified.is_(True))).one()
    users_admin = session.exec(select(func.count(User.id)).where(User.is_admin.is_(True))).one()

    # System metrics
    jobs_count = session.exec(select(func.count(Job.id))).one()
    applications_count = session.exec(select(func.count(Application.id))).one()
    sessions_count = session.exec(select(func.count(InterviewSession.id))).one()
    feedback_new = session.exec(select(func.count(Feedback.id)).where(Feedback.status == "new")).one()

    # Scheduler info
    scheduler = sched.get_scheduler()
    sched_info = {
        "running": bool(scheduler and scheduler.running),
        "jobs": [
            {
                "id": j.id,
                "name": j.name,
                "next_run": j.next_run_time.isoformat() if j.next_run_time else None,
            }
            for j in (scheduler.get_jobs() if scheduler else [])
        ],
    }

    # Connector health
    connectors: list[dict] = []
    sources = ["greenhouse", "lever", "workable", "adzuna", "careers", "hacker_news", "linkedin"]
    for s_name in sources:
        run = session.exec(
            select(SourceRun)
            .where(SourceRun.source == s_name)
            .order_by(SourceRun.started_at.desc())
        ).first()
        if run:
            connectors.append({
                "source": s_name,
                "last_run": run.started_at.isoformat(),
                "duration_ms": run.duration_ms,
                "found": run.found,
                "new_jobs": run.new_jobs,
                "ok": run.ok,
                "error": run.error,
            })
        else:
            connectors.append({
                "source": s_name,
                "last_run": None,
                "duration_ms": 0,
                "found": 0,
                "new_jobs": 0,
                "ok": True,
                "error": "No runs recorded yet",
            })

    # LLM Provider info
    now_mono = time.monotonic()
    provider_statuses = [
        {
            "name": "xAI Grok",
            "key": "xai",
            "configured": bool(settings.xai_api_key),
            "model": settings.xai_triage_model,
            "is_active": llm.active_provider == "xai",
            "on_cooldown": llm._cooldowns.get("xai", 0) > now_mono,
        },
        {
            "name": "Google Gemini",
            "key": "gemini",
            "configured": bool(settings.gemini_api_key),
            "model": settings.gemini_model,
            "is_active": llm.active_provider == "gemini",
            "on_cooldown": llm._cooldowns.get("gemini", 0) > now_mono,
        },
        {
            "name": "OpenRouter",
            "key": "openrouter",
            "configured": bool(settings.openrouter_api_key),
            "model": settings.openrouter_triage_model,
            "is_active": llm.active_provider == "openrouter",
            "on_cooldown": llm._cooldowns.get("openrouter", 0) > now_mono,
        },
        {
            "name": "Groq",
            "key": "groq",
            "configured": bool(settings.groq_api_key),
            "model": settings.groq_triage_model,
            "is_active": llm.active_provider == "groq",
            "on_cooldown": llm._cooldowns.get("groq", 0) > now_mono,
        },
        {
            "name": "Anthropic Claude",
            "key": "anthropic",
            "configured": bool(settings.anthropic_api_key),
            "model": settings.model_triage,
            "is_active": llm.active_provider == "anthropic",
            "on_cooldown": llm._cooldowns.get("anthropic", 0) > now_mono,
        },
        {
            "name": "Ollama (local)",
            "key": "ollama",
            "configured": bool(settings.ollama_host),
            "model": settings.ollama_triage_model,
            "is_active": llm.active_provider == "ollama",
            "on_cooldown": llm._cooldowns.get("ollama", 0) > now_mono,
        },
    ]

    return {
        "database": {
            "ok": True,
            "dialect": "cockroachdb" if is_cockroach() else "sqlite",
            "vector_backend": vector_backend(),
            "jobs_count": jobs_count,
            "applications_count": applications_count,
            "sessions_count": sessions_count,
            "feedback_new": feedback_new,
        },
        "scheduler": sched_info,
        "users": {
            "total": users_total,
            "active": users_active,
            "verified": users_verified,
            "admin": users_admin,
        },
        "connectors": connectors,
        "llm": {
            "available": llm.available,
            "active_provider": llm.active_provider,
            "providers": provider_statuses,
        },
        "email": {
            "configured": settings.email_enabled,
            "backend": "resend" if settings.resend_api_key else ("smtp" if settings.smtp_host else "none"),
        },
    }


@router.post("/admin/test-llm")
async def admin_test_llm(
    payload: dict = Body(default_factory=dict),
    user: User = Depends(require_admin),
) -> dict:
    """Test a specific LLM provider or default with live probe to catch expired keys."""
    import time

    from app.llm.client import get_llm

    llm = get_llm()
    provider = payload.get("provider", "") or llm.active_provider
    if not provider:
        raise HTTPException(400, "No LLM provider specified and none active")

    t0 = time.monotonic()
    try:
        res = await llm.test_provider(provider)
        latency_ms = int((time.monotonic() - t0) * 1000)
        return {
            "ok": True,
            "provider": provider,
            "model": res.model,
            "latency_ms": latency_ms,
            "reply": res.text.strip(),
        }
    except Exception as exc:  # noqa: BLE001
        latency_ms = int((time.monotonic() - t0) * 1000)
        return {
            "ok": False,
            "provider": provider,
            "latency_ms": latency_ms,
            "error": str(exc),
        }


@router.post("/admin/trigger-job/{job_name}")
def admin_trigger_job(
    job_name: str,
    user: User = Depends(require_admin),
) -> dict:
    """Trigger a scheduled background task immediately."""
    from app import scheduler as sched

    scheduler = sched.get_scheduler()
    if not scheduler or not scheduler.running:
        raise HTTPException(503, "Scheduler is not currently running")
    job = scheduler.get_job(job_name)
    if not job:
        raise HTTPException(404, f"Scheduled job {job_name!r} not found")

    job.modify(next_run_time=utcnow())
    return {"ok": True, "message": f"Job {job_name!r} scheduled for immediate execution."}

