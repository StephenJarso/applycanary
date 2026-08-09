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
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request, UploadFile
from pydantic import BaseModel, Field
from sqlmodel import Session, func, select

from app.config import get_settings
from app.db import get_session
from app.models import (
    Application,
    InterviewPrep,
    Job,
    JobAlias,
    JobScore,
    JobStatus,
    Profile,
    ResumeVersion,
    SourceRun,
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


class ProfileOut(BaseModel):
    full_name: str = ""
    email: str = ""
    phone: str = ""
    location: str = ""
    linkedin_url: str = ""
    github_username: str = ""
    portfolio_url: str = ""
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


class ProfileIn(BaseModel):
    full_name: str = ""
    email: str = ""
    phone: str = ""
    location: str = ""
    linkedin_url: str = ""
    github_username: str = ""
    portfolio_url: str = ""
    min_salary: int | None = None
    salary_currency: str = "USD"
    target_titles: list[str] = Field(default_factory=list)
    target_locations: list[str] = Field(default_factory=list)
    excluded_companies: list[str] = Field(default_factory=list)
    work_authorization: str = ""
    years_experience: int | None = None
    remote_only: bool = False


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
    auto_submit: bool
    auto_submit_min_score: int
    daily_apply_cap: int
    warnings: list[str]
    has_profile: bool


class ActionResult(BaseModel):
    ok: bool
    message: str = ""
    detail: dict[str, Any] = Field(default_factory=dict)


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


def _job_out(job: Job, score: JobScore | None) -> JobOut:
    return JobOut(
        id=job.id or 0,
        company=job.company,
        title=job.title,
        location=job.location,
        is_remote=job.is_remote,
        source=job.source,
        status=str(job.status),
        apply_url=job.apply_url,
        ats_platform=job.ats_platform,
        salary_min=job.salary_min,
        salary_max=job.salary_max,
        salary_currency=job.salary_currency,
        salary_is_estimate=job.salary_is_estimate,
        posted_at=job.posted_at,
        first_seen_at=job.first_seen_at,
        seen_count=job.seen_count,
        age_hours=round(job.age.total_seconds() / 3600, 1),
        score=_score_out(score),
    )


def _profile_or_404(session: Session) -> Profile:
    profile = session.exec(select(Profile)).first()
    if profile is None:
        raise HTTPException(404, "no profile configured; save one first")
    return profile


def _counts(session: Session) -> dict[str, int]:
    rows = session.exec(select(Job.status, func.count(Job.id)).group_by(Job.status)).all()
    counts = {str(status): n for status, n in rows}
    counts["total"] = sum(counts.values())
    return counts


# ---------------------------------------------------------------- reads


@router.get("/status", response_model=StatusOut)
def status(session: Session = Depends(get_session)) -> StatusOut:
    from app import scheduler as sched

    settings = get_settings()
    scheduler = sched.get_scheduler()
    return StatusOut(
        ok=True,
        counts=_counts(session),
        scheduler_running=bool(scheduler and scheduler.running),
        scheduled_jobs=[j.id for j in scheduler.get_jobs()] if scheduler else [],
        llm_enabled=settings.llm_enabled,
        auto_submit=settings.enable_auto_submit,
        auto_submit_min_score=settings.auto_submit_min_score,
        daily_apply_cap=settings.daily_apply_cap,
        warnings=settings.startup_warnings(),
        has_profile=session.exec(select(Profile)).first() is not None,
    )


@router.get("/jobs", response_model=JobListOut)
def list_jobs(
    session: Session = Depends(get_session),
    q: str = "",
    status: str = "",
    source: str = "",
    min_score: int = Query(0, ge=0, le=100),
    remote_only: bool = False,
    sort: Literal["score", "newest", "oldest"] = "score",
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
) -> JobListOut:
    stmt = (
        select(Job, JobScore)
        .join(JobScore, JobScore.job_id == Job.id, isouter=True)
        .where(Job.status != JobStatus.EXPIRED)
    )
    if status:
        stmt = stmt.where(Job.status == status)
    if source:
        stmt = stmt.where(Job.source == source)
    if remote_only:
        stmt = stmt.where(Job.is_remote == True)  # noqa: E712 - SQL, not Python
    if q:
        like = f"%{q}%"
        stmt = stmt.where(Job.title.ilike(like) | Job.company.ilike(like))
    if min_score:
        stmt = stmt.where(JobScore.total >= min_score)

    if sort == "newest":
        stmt = stmt.order_by(Job.first_seen_at.desc())
    elif sort == "oldest":
        stmt = stmt.order_by(Job.first_seen_at.asc())
    else:
        stmt = stmt.order_by(JobScore.total.desc().nullslast(), Job.first_seen_at.desc())

    rows = session.exec(stmt.offset(offset).limit(limit)).all()
    from app.sources import all_sources

    distinct_db_sources = set(session.exec(select(Job.source).distinct()).all())
    all_configured = set(all_sources().keys())
    sources = sorted([s for s in (distinct_db_sources | all_configured) if s])

    return JobListOut(
        jobs=[_job_out(job, score) for job, score in rows],
        total=len(rows),
        counts=_counts(session),
        sources=sources,
    )


@router.get("/jobs/{job_id}", response_model=JobDetailOut)
def job_detail(job_id: int, session: Session = Depends(get_session)) -> JobDetailOut:
    job = session.get(Job, job_id)
    if job is None:
        raise HTTPException(404, "job not found")

    score = session.exec(select(JobScore).where(JobScore.job_id == job_id)).first()
    app_row = session.exec(select(Application).where(Application.job_id == job_id)).first()
    version = (
        session.get(ResumeVersion, app_row.resume_version_id)
        if app_row and app_row.resume_version_id
        else None
    )
    prep = session.exec(select(InterviewPrep).where(InterviewPrep.job_id == job_id)).first()
    aliases = session.exec(select(JobAlias).where(JobAlias.job_id == job_id)).all()

    base = _job_out(job, score)
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
                "text": version.text,
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
            }
            if prep else None
        ),
    )


@router.get("/review", response_model=list[JobOut])
def review_queue(session: Session = Depends(get_session)) -> list[JobOut]:
    """Applications prepared but not yet submitted."""
    rows = session.exec(
        select(Job, JobScore)
        .join(Application, Application.job_id == Job.id)
        .join(JobScore, JobScore.job_id == Job.id, isouter=True)
        .where(Application.submitted_at.is_(None))
        .order_by(JobScore.total.desc().nullslast())
    ).all()
    return [_job_out(job, score) for job, score in rows]


@router.get("/applications", response_model=list[JobOut])
def applications(session: Session = Depends(get_session)) -> list[JobOut]:
    rows = session.exec(
        select(Job, JobScore)
        .join(Application, Application.job_id == Job.id)
        .join(JobScore, JobScore.job_id == Job.id, isouter=True)
        .where(Application.submitted_at.is_not(None))
        .order_by(Application.submitted_at.desc())
    ).all()
    return [_job_out(job, score) for job, score in rows]


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
def get_profile(session: Session = Depends(get_session)) -> ProfileOut:
    profile = session.exec(select(Profile)).first()
    if profile is None:
        return ProfileOut()
    evidence = profile.github_evidence or {}
    return ProfileOut(
        full_name=profile.full_name,
        email=profile.email,
        phone=profile.phone,
        location=profile.location,
        linkedin_url=profile.linkedin_url,
        github_username=profile.github_username,
        portfolio_url=profile.portfolio_url,
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
    )


# ---------------------------------------------------------------- writes


@router.put("/profile", response_model=ProfileOut)
def save_profile(
    payload: ProfileIn, session: Session = Depends(get_session)
) -> ProfileOut:
    profile = session.exec(select(Profile)).first() or Profile()
    for field, value in payload.model_dump().items():
        setattr(profile, field, value)
    profile.github_username = profile.github_username.strip().removeprefix("@")
    profile.updated_at = utcnow()
    session.add(profile)
    session.commit()
    return get_profile(session)


@router.post("/profile/resume", response_model=ActionResult)
async def upload_resume(
    request: Request, session: Session = Depends(get_session)
) -> ActionResult:
    from pathlib import Path

    from app.pipeline.keywords import extract_skills
    from app.resume.parse import SUPPORTED, parse_resume

    form = await request.form()
    upload = form.get("resume")
    if not isinstance(upload, UploadFile) or not upload.filename:
        raise HTTPException(400, "no file uploaded under field 'resume'")

    suffix = Path(upload.filename).suffix.lower()
    if suffix not in SUPPORTED:
        raise HTTPException(400, f"unsupported type {suffix}; use {sorted(SUPPORTED)}")

    settings = get_settings()
    settings.ensure_dirs()
    dest = settings.resume_dir / f"base{suffix}"
    dest.write_bytes(await upload.read())

    try:
        parsed = parse_resume(dest)
    except Exception as exc:  # noqa: BLE001
        log.exception("resume parse failed")
        raise HTTPException(400, f"could not parse resume: {exc}") from exc

    profile = session.exec(select(Profile)).first() or Profile()
    profile.base_resume_path = str(dest)
    profile.base_resume_text = parsed.text
    profile.skills = sorted(extract_skills(parsed.text))
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
def profile_ats(session: Session = Depends(get_session)) -> dict:
    """Structural ATS report for the stored resume, independent of any job."""
    from app.pipeline.ats_rules import evaluate

    profile = _profile_or_404(session)
    if not profile.base_resume_text:
        raise HTTPException(404, "no resume uploaded")
    return evaluate(profile.base_resume_text).as_dict()


@router.post("/jobs/{job_id}/tailor", response_model=ActionResult)
async def tailor(job_id: int, session: Session = Depends(get_session)) -> ActionResult:
    from app.apply.runner import prepare

    job = session.get(Job, job_id)
    if job is None:
        raise HTTPException(404, "job not found")
    profile = _profile_or_404(session)
    try:
        app_row = await prepare(session, job, profile)
    except Exception as exc:  # noqa: BLE001
        log.exception("tailoring failed for job %s", job_id)
        raise HTTPException(500, str(exc)) from exc
    return ActionResult(
        ok=True, message="Application prepared.",
        detail={"resume_version_id": app_row.resume_version_id},
    )


@router.post("/jobs/{job_id}/submit", response_model=ActionResult)
async def submit(
    job_id: int, force: bool = False, session: Session = Depends(get_session)
) -> ActionResult:
    from app.apply.runner import submit as do_submit

    job = session.get(Job, job_id)
    if job is None:
        raise HTTPException(404, "job not found")
    profile = _profile_or_404(session)
    result = await do_submit(session, job, profile, force=force)
    return ActionResult(
        ok=result.ok,
        message=result.confirmation or result.error,
        detail={"method": str(result.method), "dry_run": result.dry_run},
    )


@router.post("/jobs/{job_id}/skip", response_model=ActionResult)
def skip(job_id: int, session: Session = Depends(get_session)) -> ActionResult:
    job = session.get(Job, job_id)
    if job is None:
        raise HTTPException(404, "job not found")
    job.status = JobStatus.SKIPPED
    session.add(job)
    session.commit()
    return ActionResult(ok=True, message="Skipped.")


@router.post("/jobs/{job_id}/prep", response_model=ActionResult)
async def make_prep(job_id: int, session: Session = Depends(get_session)) -> ActionResult:
    from app.pipeline.interview import prep_for_job

    job = session.get(Job, job_id)
    if job is None:
        raise HTTPException(404, "job not found")
    profile = _profile_or_404(session)
    try:
        await prep_for_job(session, job, profile)
    except Exception as exc:  # noqa: BLE001
        log.exception("interview prep failed for job %s", job_id)
        raise HTTPException(500, str(exc)) from exc
    return ActionResult(ok=True, message="Interview prep generated.")


@router.post("/actions/poll", response_model=ActionResult)
async def trigger_poll() -> ActionResult:
    from app.pipeline.ingest import poll_broad, poll_curated

    curated = await poll_curated()
    broad = await poll_broad()
    found = curated.found + broad.found
    new = curated.new + broad.new
    return ActionResult(
        ok=True,
        message=f"Found {found} postings, {new} new.",
        detail={
            "found": found, "new": new,
            "duplicates": curated.duplicates + broad.duplicates,
            "failed_sources": curated.sources_failed + broad.sources_failed,
            "errors": [*curated.errors, *broad.errors],
        },
    )


@router.post("/actions/score", response_model=ActionResult)
async def trigger_score(session: Session = Depends(get_session)) -> ActionResult:
    from app.pipeline.score import score_pending

    count = await score_pending(session, limit=50)
    return ActionResult(ok=True, message=f"Scored {count} jobs.", detail={"scored": count})


@router.post("/actions/github", response_model=ActionResult)
async def trigger_github(session: Session = Depends(get_session)) -> ActionResult:
    from app.pipeline.github import scan

    profile = _profile_or_404(session)
    if not profile.github_username:
        raise HTTPException(400, "no GitHub username on the profile")
    evidence = await scan(profile.github_username)
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
