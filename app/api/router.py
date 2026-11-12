"""JSON API backing the React dashboard.

Mounted at /api alongside the Jinja routes in `routes.py`, which keep working
unchanged. That means the server-rendered UI stays available as a fallback and
the two can be compared directly while the SPA is built.

Response shapes are declared as Pydantic models rather than returned as loose
dicts, so FastAPI publishes an accurate schema at /docs and the TypeScript
client has something real to mirror.
"""

from __future__ import annotations

import hmac
import logging
import re
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Literal

from fastapi import APIRouter, Body, Depends, HTTPException, Query, Request
from fastapi.responses import FileResponse, RedirectResponse
from pydantic import BaseModel, Field
from sqlalchemy import and_, or_
from sqlmodel import Session, func, select

from app.config import get_settings
from app.db import get_session
from app.deps import current_user, require_admin, user_job
from app.models import (
    Application,
    CustomBoard,
    Feedback,
    InterviewPrep,
    InterviewSession,
    InviteCode,
    Job,
    JobAlias,
    JobScore,
    JobStatus,
    Profile,
    ProgrammeEntry,
    PushSubscription,
    ResumeVersion,
    SourceRun,
    User,
    UserJob,
    WatchedOpportunity,
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


class ReviewJobOut(JobOut):
    """A review row plus the address an email application would go to.

    Empty when the posting only offers a form; the UI shows it before the
    approval click so the user knows who receives their CV.
    """
    apply_email: str = ""
    # Verdict from the cover-letter gate, including the posting's requirements.
    # The queue is where applications are approved, so a blocked letter has to
    # be visible here rather than only on the job page it links to.
    letter: dict[str, Any] = Field(default_factory=dict)


class ReviewListOut(BaseModel):
    jobs: list[ReviewJobOut]
    total: int


class PipelineItemOut(BaseModel):
    """One sent application with the stage the tracker shows.

    The stage is derived rather than stored (see ``_pipeline_stage``), so a row
    reads correctly whether the change was an explicit outcome, a reply, or a
    follow-up date quietly passing.
    """

    job_id: int
    title: str
    company: str
    source: str
    apply_url: str
    method: str
    status: str
    # follow-up due | applied | responded | interview | offer | rejected | ghosted
    stage: str
    submitted_at: datetime | None = None
    follow_up_due: datetime | None = None
    response_received: bool = False
    outcome: str = ""
    days_waiting: int | None = None
    follow_up_overdue: bool = False
    score: float | None = None


class PipelineOut(BaseModel):
    items: list[PipelineItemOut]
    # Stage -> count. Fixed keys so the header chips do not disappear when a
    # stage is empty.
    counts: dict[str, int]


class PipelineUpdate(BaseModel):
    """One click's worth of stage change.

    Only the fields present are applied. ``follow_up_in_days`` is a duration
    rather than a date so the server clock decides the reminder and a client
    timezone cannot shift it.
    """

    outcome: Literal["", "interview", "offer", "rejected", "ghosted"] | None = None
    response_received: bool | None = None
    follow_up_in_days: int | None = Field(default=None, ge=0, le=120)


class OpportunityOut(BaseModel):
    """One listing on the public Events & paid programs page.

    Mirrors the dicts `app/opportunities.py` builds: live events from the TGK
    calendar plus the curated paid programs, internships and volunteer/
    speaking slots. `kind` decides whether the card counts down to an event
    date or to an application deadline.
    """

    kind: Literal["Event", "Paid program", "Internship", "Volunteer"]
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
    # Server-side verdict on the deadline, so the page does not decide "closed"
    # from the visitor's clock.
    expired: bool = False
    days_left: int | None = None


class OpportunitiesOut(BaseModel):
    items: list[OpportunityOut]
    # False when the live event feed could not be refreshed: the items are the
    # last good list (or programs only), and the UI should say so.
    events_live: bool
    events_updated_at: datetime | None = None


class EntryLevelRoleOut(BaseModel):
    """One role from work.ke's entry-level technology collection.

    ``apply_url`` is the employer's own advert, taken from the work.ke detail
    page; ``source_url`` is the work.ke listing for provenance. A role with no
    published deadline keeps its ``closing_label`` and a null ``closing_at`` —
    that means the employer published no date, not that it is open forever.
    """

    slug: str
    title: str
    employer: str
    location: str
    category: str = ""
    employer_type: str = ""
    agency: str = ""
    seniority: str = ""
    work_mode: str = ""
    is_remote: bool = False
    salary_disclosed: bool = False
    closing_at: datetime | None = None
    closing_label: str = ""
    summary: str = ""
    apply_url: str
    source_url: str
    employment_type: str = ""
    county: str = ""
    location_labels: list[str] = Field(default_factory=list)


class EntryLevelFeedOut(BaseModel):
    roles: list[EntryLevelRoleOut]
    fetched_at: datetime
    source: str
    warnings: list[str] = Field(default_factory=list)


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
    # Company (lowercased) -> ISO date the cooldown lifts.
    company_cooldowns: dict[str, str] = Field(default_factory=dict)
    work_authorization: str = ""
    years_experience: int | None = None
    remote_only: bool = False
    open_to_relocation: bool = False
    has_resume: bool = False
    resume_words: int = 0
    base_resume_filename: str = ""
    base_resume_text: str = ""
    skills: list[str] = Field(default_factory=list)
    github_synced_at: datetime | None = None
    github_repo_count: int = 0
    github_connected: bool = False
    # Email alerts: when a posting scores at/above this, email it immediately.
    alert_min_score: float = 90.0
    # Auto-submit preferences
    enable_auto_submit: bool = False
    auto_submit_min_score: float = 70.0
    daily_apply_cap: int = 5
    # Per-user LLM key
    llm_provider: str = ""
    llm_api_key_masked: str = ""  # masked, never the raw key
    # Job subscription preferences
    preferred_work_type: str = "any"
    # Email applications: whether a mailbox is connected. The password is never
    # returned — only this flag and the address it belongs to.
    email_sending_configured: bool = False
    smtp_user: str = ""


class EmailSendingIn(BaseModel):
    smtp_user: str = ""
    smtp_app_password: str = ""
    smtp_host: str = "smtp.gmail.com"
    smtp_port: int = 587
    disconnect: bool = False


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
    open_to_relocation: bool = False
    # User-confirmed skills supplement skills detected from the uploaded resume.
    skills: list[str] = Field(default_factory=list)
    # Email alerts: send immediately when a posting scores at/above this (0 = off).
    alert_min_score: float | None = None
    # Auto-submit preferences
    enable_auto_submit: bool = False
    auto_submit_min_score: float = 70.0
    daily_apply_cap: int = 5
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
    # The same verdict the daily email uses, so the page and the alert agree.
    stale: bool = False
    stale_reason: str = ""


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


_PIPELINE_OUTCOMES = ("interview", "offer", "rejected", "ghosted")
# Follow-ups first: they are the only entries with a deadline attached. Within a
# stage the oldest application is the one to deal with, which the endpoint's
# sort handles.
_PIPELINE_ORDER = {
    "follow-up due": 0,
    "applied": 1,
    "responded": 2,
    "interview": 3,
    "offer": 4,
    "rejected": 5,
    "ghosted": 6,
}


def _pipeline_stage(
    outcome: str,
    response_received: bool,
    follow_up_due: datetime | None,
    submitted_at: datetime | None,
) -> str:
    """Which column an application belongs in.

    An explicit outcome outranks everything: after the user records "offer", a
    follow-up date left over from last week must not drag the row back into the
    chase list. A reply outranks an overdue follow-up for the same reason.
    """
    if outcome in _PIPELINE_OUTCOMES:
        return outcome
    if submitted_at is None:
        return "prepared"
    if response_received:
        return "responded"
    if follow_up_due is not None and follow_up_due <= utcnow():
        return "follow-up due"
    return "applied"


def _pipeline_item(
    session: Session, user: User, app_row: Application
) -> PipelineItemOut:
    """Serialize one application for the tracker.

    Every lookup is scoped by ``user.id`` as well as ``job_id``: the posting is
    shared, the score and workflow state are not.
    """
    job = session.get(Job, app_row.job_id)
    score = session.exec(
        select(JobScore).where(
            JobScore.job_id == app_row.job_id, JobScore.user_id == user.id
        )
    ).first()
    state = session.exec(
        select(UserJob).where(
            UserJob.job_id == app_row.job_id, UserJob.user_id == user.id
        )
    ).first()
    submitted = app_row.submitted_at
    stage = _pipeline_stage(
        app_row.outcome or "",
        app_row.response_received,
        app_row.follow_up_due,
        submitted,
    )
    return PipelineItemOut(
        job_id=app_row.job_id,
        title=job.title if job else "",
        company=job.company if job else "",
        source=job.source if job else "",
        apply_url=job.apply_url if job else "",
        method=str(app_row.method),
        status=str(state.status) if state else str(JobStatus.APPLIED),
        stage=stage,
        submitted_at=submitted,
        follow_up_due=app_row.follow_up_due,
        response_received=app_row.response_received,
        outcome=app_row.outcome or "",
        days_waiting=int((utcnow() - submitted).days) if submitted else None,
        follow_up_overdue=bool(
            app_row.follow_up_due is not None and app_row.follow_up_due <= utcnow()
        ),
        score=score.total if score else None,
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


def _apply_common_filters(
    stmt: Any,
    *,
    experience_level: str = "",
    work_arrangement: str = "",
    location: str = "",
    employment_type: str = "",
    visa_sponsorship: bool = False,
    min_salary: int = 0,
) -> Any:
    if experience_level:
        exp = experience_level.lower().strip()
        if exp in ("entry", "junior"):
            stmt = stmt.where(
                or_(
                    Job.title.ilike("%intern%"),
                    Job.title.ilike("%junior%"),
                    Job.title.ilike("%jr%"),
                    Job.title.ilike("%entry%"),
                    Job.title.ilike("%graduate%"),
                    Job.title.ilike("%trainee%"),
                    Job.title.ilike("%associate%"),
                    Job.description.ilike("%entry level%"),
                    Job.description.ilike("%0-1 year%"),
                    Job.description.ilike("%0-2 year%"),
                )
            )
        elif exp == "mid":
            stmt = stmt.where(
                or_(
                    Job.title.ilike("%mid%"),
                    Job.title.ilike("%intermediate%"),
                    Job.description.ilike("%2-4 year%"),
                    Job.description.ilike("%3-5 year%"),
                    and_(
                        ~Job.title.ilike("%senior%"),
                        ~Job.title.ilike("%sr.%"),
                        ~Job.title.ilike("%sr %"),
                        ~Job.title.ilike("%lead%"),
                        ~Job.title.ilike("%staff%"),
                        ~Job.title.ilike("%principal%"),
                        ~Job.title.ilike("%manager%"),
                        ~Job.title.ilike("%director%"),
                        ~Job.title.ilike("%intern%"),
                        ~Job.title.ilike("%junior%"),
                        ~Job.title.ilike("%entry%"),
                    ),
                )
            )
        elif exp == "senior":
            stmt = stmt.where(
                or_(
                    Job.title.ilike("%senior%"),
                    Job.title.ilike("%sr.%"),
                    Job.title.ilike("%sr %"),
                    Job.title.ilike("%lead%"),
                    Job.description.ilike("%5+ year%"),
                    Job.description.ilike("%6+ year%"),
                )
            )
        elif exp in ("lead", "staff", "principal"):
            stmt = stmt.where(
                or_(
                    Job.title.ilike("%staff%"),
                    Job.title.ilike("%principal%"),
                    Job.title.ilike("%architect%"),
                )
            )
        elif exp in ("manager", "executive", "director"):
            stmt = stmt.where(
                or_(
                    Job.title.ilike("%manager%"),
                    Job.title.ilike("%director%"),
                    Job.title.ilike("%head%"),
                    Job.title.ilike("%vp%"),
                    Job.title.ilike("%chief%"),
                    Job.title.ilike("%cto%"),
                )
            )

    if work_arrangement:
        wa = work_arrangement.lower().strip()
        if wa == "remote":
            stmt = stmt.where(or_(Job.is_remote == True, Job.location.ilike("%remote%")))  # noqa: E712
        elif wa == "hybrid":
            stmt = stmt.where(or_(Job.location.ilike("%hybrid%"), Job.description.ilike("%hybrid%")))
        elif wa == "onsite":
            stmt = stmt.where(
                and_(
                    Job.is_remote == False,  # noqa: E712
                    ~Job.location.ilike("%remote%"),
                    ~Job.location.ilike("%hybrid%"),
                )
            )

    if location:
        stmt = stmt.where(Job.location.ilike(f"%{location.strip()}%"))

    if employment_type:
        et = employment_type.lower().strip()
        if et in ("full-time", "fulltime"):
            stmt = stmt.where(or_(Job.employment_type.ilike("%full%"), Job.description.ilike("%full-time%"), Job.description.ilike("%full time%")))
        elif et == "contract":
            stmt = stmt.where(or_(Job.employment_type.ilike("%contract%"), Job.description.ilike("%contract%")))
        elif et in ("internship", "intern"):
            stmt = stmt.where(or_(Job.employment_type.ilike("%intern%"), Job.title.ilike("%intern%")))
        elif et in ("part-time", "parttime"):
            stmt = stmt.where(or_(Job.employment_type.ilike("%part%"), Job.description.ilike("%part-time%")))

    if visa_sponsorship:
        stmt = stmt.where(
            or_(
                Job.description.ilike("%visa sponsorship%"),
                Job.description.ilike("%relocation package%"),
                Job.description.ilike("%relocation assistance%"),
                Job.description.ilike("%relocation support%"),
                Job.description.ilike("%sponsorship available%"),
                Job.description.ilike("%we sponsor%"),
                Job.description.ilike("%work permit%"),
                Job.description.ilike("%flight%"),
                Job.description.ilike("%airfare%"),
            )
        )

    if min_salary:
        stmt = stmt.where(
            or_(
                Job.salary_min >= min_salary,
                Job.salary_max >= min_salary,
            )
        )

    return stmt


@router.get("/public/jobs")
def public_jobs(
    session: Session = Depends(get_session),
    q: str = "",
    source: str = "",
    remote_only: bool = False,
    sort: Literal["newest", "oldest"] = "newest",
    experience_level: str = "",
    work_arrangement: str = "",
    location: str = "",
    employment_type: str = "",
    visa_sponsorship: bool = False,
    min_salary: int = Query(0, ge=0),
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

    stmt = _apply_common_filters(
        stmt,
        experience_level=experience_level,
        work_arrangement=work_arrangement,
        location=location,
        employment_type=employment_type,
        visa_sponsorship=visa_sponsorship,
        min_salary=min_salary,
    )

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
async def public_internships() -> OpportunitiesOut:
    """Curated placements plus the live Kenyan internship collection.

    Kept separate from ``/public/opportunities`` so the page can offer a
    dedicated Internships tab without every event request carrying the cards.
    The live work.ke half is cached in ``app.opportunities`` and degrades to
    the curated list with ``events_live=False`` rather than failing.
    """
    from app.opportunities import get_internships

    return OpportunitiesOut(**await get_internships())


@router.get("/public/volunteers", response_model=OpportunitiesOut)
async def public_volunteers() -> OpportunitiesOut:
    """Curated volunteer and speaking slots, public like the events page.

    Two halves: conference crew and call-for-speakers slots curated in
    ``app.opportunities``, plus upcoming events whose own description reads as
    a call for volunteers or speakers. The live half shares the event feed's
    cache, so a calendar outage degrades to the curated entries with
    ``events_live=False`` rather than emptying the page.
    """
    from app.opportunities import get_volunteers

    return OpportunitiesOut(**await get_volunteers())


@router.get("/public/kenya-jobs", response_model=EntryLevelFeedOut)
@router.get("/public/entry-level", response_model=EntryLevelFeedOut)
async def public_entry_level(refresh: bool = False) -> EntryLevelFeedOut:
    """Entry-level IT and tech roles curated by work.ke.

    Public like ``/public/jobs``: the roles carry no user data, and the page is
    useful before someone signs in. work.ke is fetched behind a 30-minute cache;
    ``refresh=true`` bypasses it. If the site is unreachable and no cached copy
    exists, say so plainly instead of showing an empty feed as "no roles".
    """
    from app.entry_level import get_entry_level

    try:
        feed = await get_entry_level(force=refresh)
    except Exception as exc:  # noqa: BLE001 - map any upstream failure to 503
        log.warning("entry-level feed failed: %s", exc)
        raise HTTPException(
            status_code=503,
            detail="work.ke could not be reached; try again in a few minutes.",
        ) from exc
    return EntryLevelFeedOut(**feed)


# work.ke slugs are lowercase words joined by hyphens and a short hash. Anything
# else is not a role this API will look up.
_ENTRY_LEVEL_SLUG_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9-]{0,199}$")


@router.get("/public/kenya-jobs/{slug}/apply")
@router.get("/public/entry-level/{slug}/apply")
async def public_entry_level_apply(slug: str) -> RedirectResponse:
    """Send a Kenya Jobs card to the employer's own advert.

    The live feed is built from work.ke's job index, which publishes slugs but
    not the employer link, so the advert is read from the role page the first
    time someone opens it (and cached). If work.ke publishes no external advert,
    or the page is unreachable, the work.ke listing is the fallback.
    """
    from app.entry_level import resolve_apply_url

    if not _ENTRY_LEVEL_SLUG_RE.fullmatch(slug):
        raise HTTPException(status_code=404, detail="Unknown role")
    return RedirectResponse(url=await resolve_apply_url(slug), status_code=307)


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
    experience_level: str = "",
    work_arrangement: str = "",
    location: str = "",
    employment_type: str = "",
    visa_sponsorship: bool = False,
    min_salary: int = Query(0, ge=0),
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

    stmt = _apply_common_filters(
        stmt,
        experience_level=experience_level,
        work_arrangement=work_arrangement,
        location=location,
        employment_type=employment_type,
        visa_sponsorship=visa_sponsorship,
        min_salary=min_salary,
    )

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
                "cover_letter_check": app_row.cover_letter_check or {},
                "form_answers": app_row.form_answers or {},
                "outcome": app_row.outcome,
                "apply_email": app_row.apply_email or "",
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
        select(Job, JobScore, UserJob, Application)
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

    from app.apply.email_smtp import application_email

    total = session.exec(select(sa_func.count()).select_from(stmt.subquery())).one()
    rows = session.exec(stmt.offset(offset).limit(limit)).all()
    return ReviewListOut(
        jobs=[
            ReviewJobOut(
                **_job_out(job, score, uj).model_dump(),
                apply_email=application_email(job),
                letter=app_row.cover_letter_check or {},
            )
            for job, score, uj, app_row in rows
        ],
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


@router.get("/pipeline", response_model=PipelineOut)
def pipeline(
    session: Session = Depends(get_session),
    user: User = Depends(current_user),
) -> PipelineOut:
    """Sent applications and what came back, for the tracker tab.

    Only rows this user has submitted: an application still waiting in the
    review queue belongs there, where it can be sent, not in the chase list.
    """
    rows = session.exec(
        select(Application)
        .where(
            Application.user_id == user.id,
            Application.submitted_at.is_not(None),
        )
        .order_by(Application.submitted_at.desc())
    ).all()

    items = [_pipeline_item(session, user, row) for row in rows]
    items.sort(key=lambda i: (_PIPELINE_ORDER.get(i.stage, 99), -(i.days_waiting or 0)))
    counts = {
        stage: sum(1 for item in items if item.stage == stage)
        for stage in _PIPELINE_ORDER
    }
    return PipelineOut(items=items, counts=counts)


async def _remember_outcome(
    session: Session, user: User, app_row: Application
) -> None:
    """Keep an outcome as agent memory as well as on the application row.

    The tracker shows the result; memory is what a later tailoring or prep pass
    can recall, so a rejection teaches the agent something instead of
    disappearing into a column nobody reads. Best-effort: the outcome is already
    committed, so an embedding failure must not turn the update into a 500.
    """
    from app.memory.vectors import save_memory

    job = session.get(Job, app_row.job_id)
    company = job.company if job else ""
    title = job.title if job else "an application"
    outcome = (app_row.outcome or "").replace("_", " ")
    try:
        await save_memory(
            session,
            user_id=user.id,
            kind="application_outcome",
            content=f"Applied to {title} at {company}; the outcome was {outcome}.",
            metadata={
                "job_id": app_row.job_id,
                "company": company,
                "outcome": app_row.outcome,
            },
        )
    except Exception:  # noqa: BLE001 - the row already carries the outcome
        log.exception("job %s: could not write outcome to memory", app_row.job_id)


@router.patch("/pipeline/{job_id}", response_model=PipelineItemOut)
async def update_pipeline(
    job_id: int,
    body: PipelineUpdate,
    session: Session = Depends(get_session),
    user: User = Depends(current_user),
) -> PipelineItemOut:
    """Record an outcome, a reply, or schedule/clear a follow-up.

    Scoped by ``user_id`` as well as ``job_id``: an application belongs to the
    account that sent it, so another user's row must read as absent here.
    """
    app_row = session.exec(
        select(Application).where(
            Application.job_id == job_id,
            Application.user_id == user.id,
        )
    ).first()
    if app_row is None or app_row.submitted_at is None:
        raise HTTPException(404, "no submitted application for this job")

    if body.outcome is not None:
        app_row.outcome = body.outcome
        if body.outcome in _PIPELINE_OUTCOMES:
            # Ghosted is the absence of a reply, not one. The other outcomes
            # all mean the employer answered, so the chase is over.
            app_row.response_received = body.outcome != "ghosted"
            app_row.follow_up_due = None

    if body.response_received is not None:
        app_row.response_received = body.response_received
        if body.response_received:
            app_row.follow_up_due = None
            app_row.follow_up_notified_at = None

    if body.follow_up_in_days is not None:
        app_row.follow_up_due = (
            None
            if body.follow_up_in_days <= 0
            else utcnow() + timedelta(days=body.follow_up_in_days)
        )
        # A new date deserves a new reminder, including one the user clears and
        # then sets again.
        app_row.follow_up_notified_at = None

    session.add(app_row)
    session.commit()
    session.refresh(app_row)

    if body.outcome in _PIPELINE_OUTCOMES:
        await _remember_outcome(session, user, app_row)
    return _pipeline_item(session, user, app_row)


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

    from app.pipeline.health import stale_sources

    for item in stale_sources(session):
        entry = by_source.get(item.source)
        if entry is not None:
            entry.stale = True
            entry.stale_reason = item.reason

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
        company_cooldowns=dict(profile.company_cooldowns or {}),
        work_authorization=profile.work_authorization,
        years_experience=profile.years_experience,
        remote_only=profile.remote_only,
        open_to_relocation=profile.open_to_relocation,
        has_resume=bool(profile.base_resume_text),
        resume_words=len((profile.base_resume_text or "").split()),
        base_resume_filename=(profile.resume_meta or {}).get("original_filename") or (Path(profile.base_resume_path).name if profile.base_resume_path else ""),
        base_resume_text=profile.base_resume_text or "",
        skills=list(profile.skills or []),
        github_synced_at=profile.github_synced_at,
        github_repo_count=len(evidence.get("repos") or []),
        github_connected=bool(profile.github_token_encrypted),
        alert_min_score=profile.alert_min_score,
        enable_auto_submit=profile.enable_auto_submit,
        auto_submit_min_score=profile.auto_submit_min_score,
        daily_apply_cap=profile.daily_apply_cap,
        llm_provider="" if user.is_admin else profile.llm_provider,
        llm_api_key_masked=masked,
        preferred_work_type=profile.preferred_work_type,
        email_sending_configured=bool(
            profile.smtp_user and profile.smtp_app_password_encrypted
        ),
        smtp_user=profile.smtp_user,
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


@router.put("/profile/email-sending", response_model=ActionResult)
def save_email_sending(
    payload: EmailSendingIn,
    session: Session = Depends(get_session),
    user: User = Depends(current_user),
) -> ActionResult:
    """Connect the mailbox that applications are emailed from.

    Gmail needs an app password (2-Step Verification on). The password is
    encrypted with the deployment secret and never returned; the API only
    reports whether a mailbox is connected. Sending still happens from the
    review queue, one approved application at a time.
    """
    from app.secrets import encrypt_secret

    profile = session.exec(
        select(Profile).where(Profile.user_id == user.id)
    ).first() or Profile(user_id=user.id)

    if payload.disconnect:
        profile.smtp_user = ""
        profile.smtp_app_password_encrypted = ""
    else:
        address = payload.smtp_user.strip()
        if "@" not in address:
            raise HTTPException(400, "Enter the email address applications are sent from")
        profile.smtp_user = address
        profile.smtp_host = (payload.smtp_host or "smtp.gmail.com").strip()
        profile.smtp_port = payload.smtp_port or 587
        # An empty password means "keep the stored one": the UI never receives
        # the secret, so it cannot echo it back on an unrelated save.
        if payload.smtp_app_password.strip():
            profile.smtp_app_password_encrypted = encrypt_secret(
                payload.smtp_app_password.strip()
            )

    profile.updated_at = utcnow()
    session.add(profile)
    session.commit()
    configured = bool(profile.smtp_user and profile.smtp_app_password_encrypted)
    return ActionResult(
        ok=True,
        message="Email sending connected." if configured else "Email sending disconnected.",
        detail={"configured": configured, "smtp_user": profile.smtp_user},
    )


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
        "original_filename": Path(upload.filename).name,
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
        detail={
            "words": parsed.word_count,
            "skills": profile.skills,
            "filename": Path(upload.filename).name,
        },
    )


@router.get("/profile/resume/download")
def download_base_resume(
    session: Session = Depends(get_session),
    user: User = Depends(current_user),
) -> FileResponse:
    """Serve the user's uploaded base resume/CV directly."""
    profile = _profile_or_404(session, user)
    if not profile.base_resume_path:
        raise HTTPException(404, "no resume uploaded yet")
    path = Path(profile.base_resume_path)
    if not path.is_file():
        raise HTTPException(404, "uploaded resume file not found on disk")

    filename = (
        (profile.resume_meta or {}).get("original_filename")
        or path.name
        or f"resume{path.suffix}"
    )
    ext = path.suffix.lower()
    media_type = (
        "application/pdf"
        if ext == ".pdf"
        else (
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
            if ext == ".docx"
            else "text/plain"
        )
    )
    return FileResponse(
        str(path),
        filename=filename,
        media_type=media_type,
        content_disposition_type="inline",
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


class CooldownIn(BaseModel):
    """How long to hide a company. 0 clears an existing cooldown."""

    months: int = Field(12, ge=0, le=24)


@router.post("/jobs/{job_id}/cooldown", response_model=ActionResult)
async def set_company_cooldown(
    job_id: int,
    body: CooldownIn,
    session: Session = Depends(get_session),
    user: User = Depends(current_user),
) -> ActionResult:
    """Hide a company for a while, or clear the block.

    Rejections routinely say "apply again in 9-12 months", but
    ``excluded_companies`` is permanent, so the only way to act on that was to
    lose the company for good. The cooldown carries an expiry and is enforced by
    the same tier-1 filter as an exclusion, so it lapses on its own.
    """
    job = session.get(Job, job_id)
    if job is None:
        raise HTTPException(404, "job not found")
    profile = _profile_or_404(session, user)
    key = (job.company or "").strip().lower()
    if not key:
        raise HTTPException(400, "this posting has no company name")

    cooldowns = dict(profile.company_cooldowns or {})
    if body.months <= 0:
        cooldowns.pop(key, None)
        message = f"Cleared the cooldown on {job.company}."
        until = ""
    else:
        until = (date.today() + timedelta(days=30 * body.months)).isoformat()
        cooldowns[key] = until
        message = f"{job.company} hidden until {until}."
    profile.company_cooldowns = cooldowns
    session.add(profile)
    session.commit()

    # Stored scores keep yesterday's verdict; re-score so the company's other
    # postings leave the feed now instead of after the next discovery cycle.
    from app.pipeline.score import rescore_scored

    rescored = await rescore_scored(session, user_id=user.id, limit=500)
    return ActionResult(
        ok=True,
        message=message,
        detail={"until": until, "rescored": rescored},
    )


class CooldownClearIn(BaseModel):
    company: str


@router.post("/profile/cooldowns/clear", response_model=ActionResult)
async def clear_company_cooldown(
    body: CooldownClearIn,
    session: Session = Depends(get_session),
    user: User = Depends(current_user),
) -> ActionResult:
    """Lift a cooldown early, so a company can be scored again immediately."""
    profile = _profile_or_404(session, user)
    key = body.company.strip().lower()
    cooldowns = dict(profile.company_cooldowns or {})
    if key not in cooldowns:
        raise HTTPException(404, "no cooldown for that company")
    cooldowns.pop(key)
    profile.company_cooldowns = cooldowns
    session.add(profile)
    session.commit()

    from app.pipeline.score import rescore_scored

    rescored = await rescore_scored(session, user_id=user.id, limit=500)
    return ActionResult(
        ok=True,
        message=f"Cleared the cooldown on {body.company}.",
        detail={"rescored": rescored},
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


@router.post("/jobs/{job_id}/save", response_model=ActionResult)
def save_job(
    job_id: int,
    session: Session = Depends(get_session),
    user: User = Depends(current_user),
) -> ActionResult:
    """Toggle save/bookmark on a job."""
    job = session.get(Job, job_id)
    if job is None:
        raise HTTPException(404, "job not found")
    state = user_job(session, user.id, job_id)
    if state.status == JobStatus.SAVED:
        state.status = JobStatus.NEW
        session.add(state)
        session.commit()
        return ActionResult(ok=True, message="Removed from saved jobs.")
    state.status = JobStatus.SAVED
    session.add(state)
    session.commit()
    return ActionResult(ok=True, message="Saved for later.")


class ApplyEmailIn(BaseModel):
    apply_email: str


@router.put("/jobs/{job_id}/apply-email", response_model=ActionResult)
def set_apply_email(
    job_id: int,
    payload: ApplyEmailIn,
    session: Session = Depends(get_session),
    user: User = Depends(current_user),
) -> ActionResult:
    """Set a custom application email for this job (used when posting has no mailto)."""
    job = session.get(Job, job_id)
    if job is None:
        raise HTTPException(404, "job not found")
    app = session.exec(
        select(Application).where(
            Application.job_id == job_id, Application.user_id == user.id
        )
    ).first()
    if app is None:
        app = Application(job_id=job_id, user_id=user.id)
    email = payload.apply_email.strip()
    if email and "@" not in email:
        raise HTTPException(400, "Enter a valid email address")
    app.apply_email = email
    session.add(app)
    session.commit()
    return ActionResult(ok=True, message="Application email updated." if email else "Application email cleared.")


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


@router.post("/cron/poll", response_model=ActionResult)
async def cron_poll(
    request: Request,
    token: str = Query("", description="CRON_TOKEN, or send X-Cron-Token instead."),
) -> ActionResult:
    """Poll the boards without a session, so an external cron can drive it.

    The scheduler runs inside the web process (``app/main.py``), so a host that
    sleeps — Render's free tier, a stopped container — silently stops polling.
    This route lets a cheap outside cron keep the feed alive.

    It fails closed: with no ``CRON_TOKEN`` configured it refuses rather than
    serving an unauthenticated write path, and it does nothing while the
    scheduler is running, so the two drivers cannot double-poll.
    """
    settings = get_settings()
    if not settings.cron_token:
        raise HTTPException(503, "CRON_TOKEN is not configured")
    supplied = request.headers.get("X-Cron-Token") or token
    if not hmac.compare_digest(supplied, settings.cron_token):
        raise HTTPException(401, "bad cron token")

    from app import scheduler as sched

    scheduler = sched.get_scheduler()
    if scheduler is not None and scheduler.running:
        return ActionResult(
            ok=True,
            message="The scheduler is running; no extra poll needed.",
            detail={"skipped": True},
        )

    from app.pipeline.ingest import poll_broad, poll_curated

    curated = await poll_curated()
    broad = await poll_broad()
    # Polling without scoring leaves the feed full of unscored rows, which is
    # exactly the state this route exists to prevent on a scheduler-less host.
    await sched.job_score_new()

    run = curated.sources_run + broad.sources_run
    new = curated.new + broad.new
    failed = curated.sources_failed + broad.sources_failed
    return ActionResult(
        ok=True,
        message=f"Polled {run} sources: {new} new, {failed} failed.",
        detail={"sources": run, "new": new, "failed": failed, "scored": True},
    )


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


@router.post("/actions/rescore", response_model=ActionResult)
async def trigger_rescore(
    limit: int = Query(50, ge=1, le=200),
    session: Session = Depends(get_session),
    user: User = Depends(current_user),
) -> ActionResult:
    """Re-evaluate this user's already-scored jobs.

    Filters and prompts improve; persisted decisions do not update themselves,
    so without this a relevance fix only helps postings found later while the
    dashboard keeps ranking the old mistakes at the top.
    """
    from app.pipeline.score import rescore_scored

    count = await rescore_scored(session, user_id=user.id, limit=limit)
    return ActionResult(
        ok=True,
        message=f"Re-scored {count} jobs against the current filters.",
        detail={"rescored": count},
    )


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

    owner_id = _superuser_id(session)
    users_rows = session.exec(select(User).order_by(User.created_at.desc())).all()
    profiles_map = {p.user_id: p for p in session.exec(select(Profile)).all()}
    user_list = [
        {
            "id": u.id,
            "email": u.email,
            "is_admin": u.is_admin,
            # The first admin (lowest id) is the instance owner. It cannot be
            # demoted, deactivated or deleted by anyone, including itself.
            "is_superuser": u.id == owner_id,
            "is_active": u.is_active,
            "email_verified": u.email_verified,
            "plan": "paid" if getattr(profiles_map.get(u.id), "llm_api_key", "") else "free",
            "last_login_at": u.last_login_at.isoformat() if u.last_login_at else None,
            "created_at": u.created_at.isoformat(),
        }
        for u in users_rows
    ]

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
    sources = [
        "greenhouse", "lever", "ashby", "workable", "adzuna", "careers",
        "brightermonday", "fuzu", "myjobmag", "careerpointkenya",
        "remoteok", "remotive", "jobicy", "himalayas", "arbeitnow",
        "websearch", "linkedin", "hn_hiring"
    ]
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
            "user_list": user_list,
        },
        "connectors": connectors,
        "llm": {
            "available": llm.available,
            "active_provider": llm.active_provider,
            "providers": provider_statuses,
        },
        "email": {
            "configured": settings.email_enabled,
            "backend": (
                "brevo" if settings.brevo_api_key
                else "resend" if settings.resend_api_key
                else "smtp" if settings.smtp_host
                else "none"
            ),
            # Delivery problems are almost always visible in these three: an
            # unverified sender, a sandbox sender that only reaches the account
            # owner, or no absolute origin (which skips the send entirely).
            "from": settings.email_from,
            "link_base": settings.email_link_base,
            "verification_required": settings.require_email_verification,
        },
    }


@router.get("/admin/email-events")
async def admin_email_events(
    email: str = Query(..., min_length=3, max_length=320),
    admin: User = Depends(require_admin),
) -> dict:
    """Delivery verdict from Brevo for one recipient's messages.

    The send call only proves the provider accepted the message; the receiving
    server decides afterwards whether it is delivered, deferred or blocked. A
    user reporting "I never got the verification email" is usually answered
    here — blocked sender authentication, a sandbox sender, or a hard bounce —
    rather than by guessing.
    """
    from app.notify import email as mail

    recipient = email.strip().lower()
    settings = get_settings()
    config = {
        "backend": (
            "brevo" if settings.brevo_api_key
            else "resend" if settings.resend_api_key
            else "smtp" if settings.smtp_host
            else "none"
        ),
        "from": settings.email_from,
        "link_base": settings.email_link_base,
        "verification_required": settings.require_email_verification,
    }
    try:
        events = await mail.brevo_delivery_events(recipient)
    except Exception as exc:  # noqa: BLE001 — the reason *is* the payload here
        log.warning("email delivery events unavailable for %s: %s", recipient, exc)
        return {
            "ok": False, "email": recipient, "events": [],
            "config": config, "error": str(exc),
        }
    return {"ok": True, "email": recipient, "events": events, "config": config, "error": ""}


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


# ---------------------------------------------------------------- push / watch


class PushSubIn(BaseModel):
    endpoint: str
    p256dh: str
    auth: str


class WatchIn(BaseModel):
    url: str
    title: str = ""
    org: str = ""
    kind: str = ""
    deadline: str | None = None
    opens: str | None = None


@router.get("/push/vapid-public-key")
def get_vapid_public_key() -> dict:
    """Return VAPID public key for push subscription (public endpoint)."""
    return {"publicKey": get_settings().vapid_public_key}


@router.post("/push/subscribe")
def push_subscribe(
    body: PushSubIn,
    user: User = Depends(current_user),
    session: Session = Depends(get_session),
) -> dict:
    """Save or refresh a browser push subscription for the current user."""
    existing = session.exec(
        select(PushSubscription).where(
            PushSubscription.user_id == user.id,
            PushSubscription.endpoint == body.endpoint,
        )
    ).first()
    if existing:
        existing.p256dh = body.p256dh
        existing.auth = body.auth
        session.add(existing)
    else:
        session.add(
            PushSubscription(
                user_id=user.id,
                endpoint=body.endpoint,
                p256dh=body.p256dh,
                auth=body.auth,
            )
        )
    session.commit()
    return {"ok": True}


@router.delete("/push/unsubscribe")
def push_unsubscribe(
    user: User = Depends(current_user),
    session: Session = Depends(get_session),
) -> dict:
    """Remove all push subscriptions for the current user."""
    subs = session.exec(
        select(PushSubscription).where(PushSubscription.user_id == user.id)
    ).all()
    for s in subs:
        session.delete(s)
    session.commit()
    return {"ok": True}


@router.get("/opportunities/watched")
def get_watched(
    user: User = Depends(current_user),
    session: Session = Depends(get_session),
) -> list[dict]:
    """Return all watched opportunities for the current user.

    Only rows whose alert is still on: an application the user tracked and then
    silenced stays in the tracker, not in this list.
    """
    rows = session.exec(
        select(WatchedOpportunity).where(
            WatchedOpportunity.user_id == user.id,
            WatchedOpportunity.watched == True,  # noqa: E712 - SQL, not Python
        )
    ).all()
    return [_opportunity_row(r) for r in rows]


@router.post("/opportunities/watch")
def watch_opp(
    body: WatchIn,
    user: User = Depends(current_user),
    session: Session = Depends(get_session),
) -> dict:
    """Subscribe to deadline notifications for an opportunity."""
    existing = session.exec(
        select(WatchedOpportunity).where(
            WatchedOpportunity.user_id == user.id,
            WatchedOpportunity.opp_url == body.url,
        )
    ).first()
    if existing is None:
        session.add(
            WatchedOpportunity(
                user_id=user.id,
                opp_url=body.url,
                opp_title=body.title,
                opp_org=body.org,
                opp_kind=body.kind,
                deadline=body.deadline,
                opens=body.opens,
            )
        )
    else:
        # Re-watching must not wipe the tracking the row already carries, and
        # the card is the freshest source for the metadata.
        existing.watched = True
        existing.opp_title = body.title or existing.opp_title
        existing.opp_org = body.org or existing.opp_org
        existing.opp_kind = body.kind or existing.opp_kind
        existing.deadline = body.deadline or existing.deadline
        existing.opens = body.opens or existing.opens
        session.add(existing)
    session.commit()
    return {"ok": True, "watched": True}


@router.delete("/opportunities/watch")
def unwatch_opp(
    body: WatchIn,
    user: User = Depends(current_user),
    session: Session = Depends(get_session),
) -> dict:
    """Remove deadline notification subscription for an opportunity."""
    row = session.exec(
        select(WatchedOpportunity).where(
            WatchedOpportunity.user_id == user.id,
            WatchedOpportunity.opp_url == body.url,
        )
    ).first()
    if row is not None:
        if row.applied_at is not None or row.outcome:
            # Turn off the alert but keep the application history: the user is
            # saying "stop reminding me", not "forget that I applied".
            row.watched = False
            session.add(row)
        else:
            session.delete(row)
        session.commit()
    return {"ok": True, "watched": False}


def _opportunity_stage(row: WatchedOpportunity) -> str:
    """Which stage an opportunity application has reached.

    Mirrors the job tracker: an explicit verdict outranks a reply, and a row
    the user only watches (never applied to) is not part of the pipeline at all.
    """
    if row.outcome in _PIPELINE_OUTCOMES:
        return row.outcome
    if row.applied_at is None:
        return "watching"
    if row.response_received:
        return "responded"
    return "applied"


def _opportunity_row(row: WatchedOpportunity) -> dict:
    return {
        "id": row.id,
        "opp_url": row.opp_url,
        "opp_title": row.opp_title,
        "opp_org": row.opp_org,
        "opp_kind": row.opp_kind,
        "deadline": row.deadline,
        "opens": row.opens,
        "watched": row.watched,
        "applied_at": row.applied_at,
        "response_received": row.response_received,
        "outcome": row.outcome or "",
        "stage": _opportunity_stage(row),
        "created_at": row.created_at.isoformat(),
    }


class OpportunityStatusIn(BaseModel):
    """One click on an opportunity card or in the Pipeline tracker."""

    url: str = Field(min_length=8, max_length=1000)
    title: str = ""
    org: str = ""
    kind: str = ""
    deadline: str | None = None
    status: Literal["", "applied", "heard_back", "interview", "offer", "rejected", "ghosted"]


@router.get("/opportunities/tracked")
def list_tracked_opportunities(
    user: User = Depends(current_user),
    session: Session = Depends(get_session),
) -> list[dict]:
    """Opportunities the user has applied to, with the stage they reached.

    Watching alone is not an application: an alert-only row would otherwise
    appear in the tracker as if it had been sent.
    """
    rows = session.exec(
        select(WatchedOpportunity).where(
            WatchedOpportunity.user_id == user.id,
            or_(
                WatchedOpportunity.applied_at.is_not(None),
                WatchedOpportunity.outcome != "",
            ),
        )
    ).all()
    items = [_opportunity_row(r) for r in rows]
    items.sort(key=lambda i: (_PIPELINE_ORDER.get(i["stage"], 99), i["opp_title"]))
    return items


@router.patch("/opportunities/tracked")
def track_opportunity(
    body: OpportunityStatusIn,
    user: User = Depends(current_user),
    session: Session = Depends(get_session),
) -> dict:
    """Mark an opportunity applied, replied to, or closed with a verdict."""
    row = session.exec(
        select(WatchedOpportunity).where(
            WatchedOpportunity.user_id == user.id,
            WatchedOpportunity.opp_url == body.url,
        )
    ).first()
    if row is None:
        row = WatchedOpportunity(
            user_id=user.id,
            opp_url=body.url,
            opp_title=body.title,
            opp_org=body.org,
            opp_kind=body.kind,
            deadline=body.deadline,
            # Tracking is not the same as asking for a deadline push.
            watched=False,
        )
    else:
        row.opp_title = body.title or row.opp_title
        row.opp_org = body.org or row.opp_org
        row.opp_kind = body.kind or row.opp_kind
        row.deadline = body.deadline or row.deadline

    if body.status:
        # Anything past watching means they applied; the date anchors sorting
        # and the "how long has it been out" column.
        if row.applied_at is None:
            row.applied_at = utcnow()

    if body.status == "applied":
        row.outcome = ""
        row.response_received = False
    elif body.status == "heard_back":
        row.response_received = True
    elif body.status in _PIPELINE_OUTCOMES:
        row.outcome = body.status
        row.response_received = body.status != "ghosted"
    elif body.status == "":
        # Reopen: withdraw the verdict but keep the fact that they applied.
        row.outcome = ""

    session.add(row)
    session.commit()
    session.refresh(row)
    return _opportunity_row(row)


# ---------------------------------------------------------------- admin management


def _superuser_id(session: Session) -> int | None:
    """Id of the instance owner: the earliest admin still holding the role.

    There is no separate column, so "the first admin" is the stable identity.
    It is protected from demotion/deactivation/deletion by every admin —
    otherwise an accidental click (or a hostile second admin) can orphan the
    instance. Use the bootstrap account for this role.
    """
    return session.exec(
        select(func.min(User.id)).where(User.is_admin.is_(True))
    ).one()


@router.patch("/admin/users/{user_id}/role")
def admin_set_user_role(
    user_id: int,
    body: dict = Body(...),
    admin: User = Depends(require_admin),
    session: Session = Depends(get_session),
) -> dict:
    """Toggle admin status or active status for a user.

    Self-service demotion/deactivation is refused: it is a one-click way to
    lock yourself out, and another admin can always make the change instead.
    The last *active* admin is protected in both directions, including when
    the other admins exist but are deactivated.
    """
    u = session.get(User, user_id)
    if not u:
        raise HTTPException(404, "User not found")

    def other_active_admins() -> int:
        return session.exec(
            select(func.count(User.id)).where(
                User.is_admin.is_(True),
                User.is_active.is_(True),
                User.id != u.id,
            )
        ).one()

    superuser_id = _superuser_id(session)
    if "is_admin" in body:
        make_admin = bool(body["is_admin"])
        if u.is_admin and not make_admin:
            if u.id == admin.id:
                raise HTTPException(400, "You cannot remove your own admin role")
            if u.id == superuser_id:
                raise HTTPException(400, "The superuser account cannot be demoted")
            if u.is_active and other_active_admins() == 0:
                raise HTTPException(400, "Cannot remove the only active administrator")
        u.is_admin = make_admin
    if "is_active" in body:
        active = bool(body["is_active"])
        if u.is_admin and not active:
            if u.id == admin.id:
                raise HTTPException(400, "You cannot deactivate your own account")
            if u.id == superuser_id:
                raise HTTPException(400, "The superuser account cannot be deactivated")
            if other_active_admins() == 0:
                raise HTTPException(400, "Cannot deactivate the only active administrator")
        u.is_active = active
    session.add(u)
    session.commit()
    return {"ok": True}


@router.delete("/admin/users/{user_id}")
def admin_delete_user(
    user_id: int,
    admin: User = Depends(require_admin),
    session: Session = Depends(get_session),
) -> dict:
    """Delete a user account."""
    if user_id == admin.id:
        raise HTTPException(400, "Cannot delete your own account")
    u = session.get(User, user_id)
    if not u:
        raise HTTPException(404, "User not found")
    if u.is_admin:
        if u.id == _superuser_id(session):
            raise HTTPException(400, "The superuser account cannot be deleted")
        admin_count = session.exec(select(func.count(User.id)).where(User.is_admin.is_(True))).one()
        if admin_count <= 1:
            raise HTTPException(400, "Cannot delete the only administrator")
        if u.is_active:
            other_active = session.exec(
                select(func.count(User.id)).where(
                    User.is_admin.is_(True),
                    User.is_active.is_(True),
                    User.id != u.id,
                )
            ).one()
            if other_active == 0:
                raise HTTPException(400, "Cannot delete the only active administrator")
    session.delete(u)
    session.commit()
    return {"ok": True}


@router.patch("/admin/settings/llm-keys")
def admin_update_llm_keys(
    body: dict = Body(...),
    admin: User = Depends(require_admin),
) -> dict:
    """Persist LLM provider API keys and model names to the process-level settings in memory."""
    import app.llm.client as client_mod
    from app.config import get_settings

    s = get_settings()
    updated: list[str] = []

    # API key fields
    key_map = {
        "xai": "xai_api_key",
        "gemini": "gemini_api_key",
        "openrouter": "openrouter_api_key",
        "groq": "groq_api_key",
        "anthropic": "anthropic_api_key",
    }
    for provider, field in key_map.items():
        val = body.get(provider)
        if isinstance(val, str) and val.strip():
            object.__setattr__(s, field, val.strip())
            updated.append(provider)

    # Model name fields (optional; only applied when non-empty)
    model_fields = [
        "xai_triage_model",
        "xai_tailor_model",
        "gemini_model",
        "gemini_tailor_model",
        "openrouter_triage_model",
        "openrouter_tailor_model",
        "groq_triage_model",
        "groq_tailor_model",
        "ollama_host",
        "ollama_triage_model",
        "ollama_tailor_model",
        "model_triage",
        "model_tailor",
    ]
    models_updated: list[str] = []
    for field in model_fields:
        val = body.get(field)
        if isinstance(val, str) and val.strip():
            object.__setattr__(s, field, val.strip())
            models_updated.append(field)

    # Re-initialize the LLM client so new keys/models take effect immediately
    if updated or models_updated:
        client_mod._client = None

    return {
        "ok": True,
        "updated": updated,
        "models_updated": models_updated,
        "note": "Keys and models updated live. Set environment variables to persist across server restarts.",
    }


class BoardIn(BaseModel):
    url: str = Field(min_length=8, max_length=500)
    label: str = ""


class BoardUpdateIn(BaseModel):
    label: str | None = Field(default=None, max_length=120)
    enabled: bool | None = None


class BoardOut(BaseModel):
    id: int
    url: str
    kind: str
    token: str
    label: str
    enabled: bool


class BoardPreviewOut(BaseModel):
    kind: str
    token: str
    label: str
    found: int
    samples: list[dict[str, str]]


def _board_out(row: CustomBoard) -> BoardOut:
    return BoardOut(
        id=row.id or 0,
        url=row.url,
        kind=row.kind,
        token=row.token,
        label=row.label,
        enabled=row.enabled,
    )


@router.get("/admin/boards", response_model=list[BoardOut])
def admin_boards(
    session: Session = Depends(get_session),
    _admin: User = Depends(require_admin),
) -> list[BoardOut]:
    rows = session.exec(select(CustomBoard).order_by(CustomBoard.label)).all()
    return [_board_out(row) for row in rows]


@router.post("/admin/boards/preview", response_model=BoardPreviewOut)
async def preview_board(
    body: BoardIn,
    _admin: User = Depends(require_admin),
) -> BoardPreviewOut:
    """Parse a board once without saving it, so a typo fails before it polls."""
    from app.sources.boards import preview

    try:
        result = await preview(body.url)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    except Exception as exc:  # noqa: BLE001 - report the site's failure as a 502
        raise HTTPException(502, f"could not read that board: {exc}") from exc
    return BoardPreviewOut(**result)


@router.post("/admin/boards", response_model=BoardOut)
def add_board(
    body: BoardIn,
    session: Session = Depends(get_session),
    admin: User = Depends(require_admin),
) -> BoardOut:
    from urllib.parse import urlparse

    from app.sources.boards import recognise

    url = body.url.strip()
    try:
        kind, token = recognise(url)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc

    if session.exec(select(CustomBoard).where(CustomBoard.url == url)).first():
        raise HTTPException(409, "that board is already added")

    row = CustomBoard(
        url=url,
        kind=kind,
        token=token,
        label=body.label.strip() or urlparse(url).netloc,
        created_by=admin.id,
    )
    session.add(row)
    session.commit()
    session.refresh(row)
    return _board_out(row)


@router.post("/admin/boards/{board_id}/poll", response_model=ActionResult)
async def poll_board_now(
    board_id: int,
    session: Session = Depends(get_session),
    _admin: User = Depends(require_admin),
) -> ActionResult:
    """Fetch one board immediately instead of waiting for the next cycle.

    Adding or resuming a board used to mean up to five silent minutes before
    anything appeared, which reads as "the URL did not work". The run is
    recorded as a SourceRun like any other, so a bad URL still shows up on the
    Sources page.
    """
    import httpx

    from app.pipeline.ingest import run_sources
    from app.sources.base import DEFAULT_TIMEOUT, USER_AGENT
    from app.sources.boards import build_board

    row = session.get(CustomBoard, board_id)
    if row is None:
        raise HTTPException(404, "board not found")

    async with httpx.AsyncClient(
        timeout=DEFAULT_TIMEOUT,
        headers={"User-Agent": USER_AGENT},
        follow_redirects=True,
    ) as client:
        source = build_board(row.kind, row.token, row.label, row.url, client)
        summary = await run_sources([source])

    label = row.label or row.url
    if summary.sources_failed:
        detail = summary.errors[0] if summary.errors else "unknown error"
        return ActionResult(
            ok=False,
            message=f"Could not poll {label}: {detail}",
            detail={"found": 0, "new": 0},
        )
    return ActionResult(
        ok=True,
        message=f"Polled {label}: {summary.found} found, {summary.new} new.",
        detail={"found": summary.found, "new": summary.new},
    )


@router.patch("/admin/boards/{board_id}", response_model=BoardOut)
def update_board(
    board_id: int,
    body: BoardUpdateIn,
    session: Session = Depends(get_session),
    _admin: User = Depends(require_admin),
) -> BoardOut:
    """Rename or pause a board without losing its URL and recognition."""
    row = session.get(CustomBoard, board_id)
    if row is None:
        raise HTTPException(404, "board not found")
    if body.label is not None:
        label = body.label.strip()
        if not label:
            raise HTTPException(422, "label cannot be blank")
        row.label = label
    if body.enabled is not None:
        row.enabled = body.enabled
    session.add(row)
    session.commit()
    session.refresh(row)
    return _board_out(row)


@router.delete("/admin/boards/{board_id}", response_model=ActionResult)
def delete_board(
    board_id: int,
    session: Session = Depends(get_session),
    _admin: User = Depends(require_admin),
) -> ActionResult:
    row = session.get(CustomBoard, board_id)
    if row is None:
        raise HTTPException(404, "board not found")
    session.delete(row)
    session.commit()
    return ActionResult(ok=True, message=f"Removed {row.label or row.url}.")


class ProgrammeIn(BaseModel):
    """One curated card as an admin submits it, mirroring the public shape."""

    kind: Literal["Paid program", "Internship", "Apprenticeship", "Volunteer"]
    title: str = Field(min_length=1, max_length=200)
    org: str = ""
    when: str = ""
    opens: str | None = None
    closes: str | None = None
    date: str | None = None
    steps: list[str] | None = None
    city: str = ""
    tags: list[str] = Field(default_factory=list)
    stipend: str | None = None
    stipend_amount: int | None = None
    stipend_currency: str = ""
    stipend_period: str = ""
    accommodation: Literal["included", "not_included", "not_stated"] = "not_stated"
    airfare_covered: bool = False
    relocation_support: bool = False
    remote_friendly: bool = False
    eligibility: list[str] = Field(default_factory=list)
    paid_to_learn: bool = False
    source: str = "Admin"
    url: str = ""
    desc: str = ""


class ProgrammeImportIn(BaseModel):
    items: list[ProgrammeIn]


class ProgrammeOut(ProgrammeIn):
    id: int
    expired: bool = False
    days_left: int | None = None
    # open | opening_soon | closed | rolling, computed server-side.
    status: str = "rolling"
    opens_in: int | None = None


def _programme_out(row: ProgrammeEntry) -> ProgrammeOut:
    from app.opportunities import programme_item

    return ProgrammeOut(id=row.id or 0, **programme_item(row))


def _upsert_programme(session: Session, body: ProgrammeIn) -> ProgrammeEntry:
    """Create or update the row with the same kind and title."""
    from app.opportunities import programme_fields

    fields = programme_fields(body.model_dump())
    if not fields["title"]:
        raise HTTPException(422, "title cannot be blank")
    url = fields["url"]
    if url and not url.startswith(("http://", "https://")):
        raise HTTPException(422, "url must start with http:// or https://")

    row = session.exec(
        select(ProgrammeEntry).where(
            ProgrammeEntry.kind == fields["kind"],
            ProgrammeEntry.title == fields["title"],
        )
    ).first()
    if row is None:
        row = ProgrammeEntry(**fields)
    else:
        for key, value in fields.items():
            setattr(row, key, value)
        # Importing a retired card is how an admin restores it.
        row.retired = False
        row.updated_at = utcnow()
    session.add(row)
    session.commit()
    session.refresh(row)
    return row


@router.get("/admin/programmes", response_model=list[ProgrammeOut])
def admin_programmes(
    session: Session = Depends(get_session),
    _admin: User = Depends(require_admin),
) -> list[ProgrammeOut]:
    """Every curated card, including ones the public pages are not showing yet."""
    rows = session.exec(
        select(ProgrammeEntry)
        .where(ProgrammeEntry.retired.is_(False))
        .order_by(ProgrammeEntry.kind, ProgrammeEntry.title)
    ).all()
    return [_programme_out(row) for row in rows]


@router.post("/admin/programmes", response_model=ProgrammeOut)
def create_programme(
    body: ProgrammeIn,
    session: Session = Depends(get_session),
    _admin: User = Depends(require_admin),
) -> ProgrammeOut:
    """Add a card, or update the one with the same kind and title."""
    row = _upsert_programme(session, body)
    return _programme_out(row)


@router.post("/admin/programmes/import", response_model=ActionResult)
def import_programmes(
    body: ProgrammeImportIn,
    session: Session = Depends(get_session),
    _admin: User = Depends(require_admin),
) -> ActionResult:
    """Bulk add/update from a JSON list, so a whole cycle can be pasted in."""
    created = updated = 0
    for payload in body.items:
        existed = session.exec(
            select(ProgrammeEntry).where(
                ProgrammeEntry.kind == payload.kind,
                ProgrammeEntry.title == payload.title.strip(),
            )
        ).first()
        _upsert_programme(session, payload)
        created += existed is None
        updated += existed is not None
    return ActionResult(
        ok=True,
        message=f"Imported {len(body.items)} cards: {created} new, {updated} updated.",
        detail={"created": created, "updated": updated},
    )


@router.patch("/admin/programmes/{entry_id}", response_model=ProgrammeOut)
def update_programme(
    entry_id: int,
    body: dict[str, Any] = Body(...),
    session: Session = Depends(get_session),
    _admin: User = Depends(require_admin),
) -> ProgrammeOut:
    """Edit part of a card in place, so a typo does not need a JSON repaste."""
    from app.opportunities import PROGRAMME_KINDS, programme_fields, programme_item

    row = session.get(ProgrammeEntry, entry_id)
    if row is None:
        raise HTTPException(404, "programme not found")

    merged = programme_item(row)
    merged.update({key: value for key, value in body.items() if key in merged})
    fields = programme_fields(merged)

    if fields["kind"] not in PROGRAMME_KINDS:
        raise HTTPException(422, f"kind must be one of {', '.join(PROGRAMME_KINDS)}")
    if not fields["title"]:
        raise HTTPException(422, "title cannot be blank")
    url = fields["url"]
    if url and not url.startswith(("http://", "https://")):
        raise HTTPException(422, "url must start with http:// or https://")

    clash = session.exec(
        select(ProgrammeEntry).where(
            ProgrammeEntry.kind == fields["kind"],
            ProgrammeEntry.title == fields["title"],
            ProgrammeEntry.id != entry_id,
        )
    ).first()
    if clash is not None:
        raise HTTPException(409, "another card already has that kind and title")

    for key, value in fields.items():
        setattr(row, key, value)
    row.updated_at = utcnow()
    session.add(row)
    session.commit()
    session.refresh(row)
    return _programme_out(row)


@router.delete("/admin/programmes/{entry_id}", response_model=ActionResult)
def delete_programme(
    entry_id: int,
    session: Session = Depends(get_session),
    _admin: User = Depends(require_admin),
) -> ActionResult:
    row = session.get(ProgrammeEntry, entry_id)
    if row is None or row.retired:
        raise HTTPException(404, "programme not found")
    title = row.title
    # Soft delete: the seed runs on every boot and would re-create a deleted
    # built-in card. Retiring keeps the tombstone that stops that.
    row.retired = True
    row.updated_at = utcnow()
    session.add(row)
    session.commit()
    return ActionResult(ok=True, message=f"Deleted {title}.")


@router.get("/admin/source-runs")
def admin_get_source_runs(
    limit: int = Query(default=50, ge=1, le=100),
    admin: User = Depends(require_admin),
    session: Session = Depends(get_session),
) -> list[dict]:
    """Recent source runs across all connectors."""
    runs = session.exec(
        select(SourceRun).order_by(SourceRun.started_at.desc()).limit(limit)
    ).all()
    return [
        {
            "id": r.id,
            "source": r.source,
            "started_at": r.started_at.isoformat(),
            "duration_ms": r.duration_ms,
            "found": r.found,
            "new_jobs": r.new_jobs,
            "duplicates": r.duplicates,
            "ok": r.ok,
            "error": r.error,
        }
        for r in runs
    ]


@router.get("/admin/invites")
def admin_get_invites(
    admin: User = Depends(require_admin),
    session: Session = Depends(get_session),
) -> list[dict]:
    """List all invite codes."""
    invites = session.exec(
        select(InviteCode).order_by(InviteCode.created_at.desc())
    ).all()
    return [
        {
            "id": inv.id,
            "code": inv.code,
            "used": inv.used_at is not None,
            "used_at": inv.used_at.isoformat() if inv.used_at else None,
            "expires_at": inv.expires_at.isoformat() if inv.expires_at else None,
            "created_at": inv.created_at.isoformat(),
        }
        for inv in invites
    ]


@router.post("/admin/invites")
def admin_create_invite(
    admin: User = Depends(require_admin),
    session: Session = Depends(get_session),
) -> dict:
    """Generate a new invite code."""
    import secrets

    code = secrets.token_urlsafe(12)
    inv = InviteCode(code=code, created_by_id=admin.id)
    session.add(inv)
    session.commit()
    return {"ok": True, "code": inv.code, "link": f"/register?invite_code={inv.code}"}


@router.delete("/admin/invites/{code}")
def admin_revoke_invite(
    code: str,
    admin: User = Depends(require_admin),
    session: Session = Depends(get_session),
) -> dict:
    """Revoke/delete an unused invite code."""
    inv = session.exec(select(InviteCode).where(InviteCode.code == code)).first()
    if not inv:
        raise HTTPException(404, "Invite code not found")
    session.delete(inv)
    session.commit()
    return {"ok": True}



