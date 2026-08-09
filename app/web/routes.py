"""Dashboard routes.

Server-rendered Jinja templates, no build step. Actions that mutate state are
POSTs that redirect back, so the browser back button never re-submits an
application.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlmodel import Session, func, select

from app.config import get_settings
from app.db import get_session
from app.models import (
    Application,
    InterviewPrep,
    Job,
    JobScore,
    JobStatus,
    Profile,
    ResumeVersion,
)

log = logging.getLogger(__name__)

router = APIRouter()
templates = Jinja2Templates(directory=str(get_settings().templates_dir))


def _profile(session: Session) -> Profile | None:
    return session.exec(select(Profile)).first()


def _counts(session: Session) -> dict[str, int]:
    rows = session.exec(
        select(Job.status, func.count(Job.id)).group_by(Job.status)
    ).all()
    counts = {str(status): n for status, n in rows}
    counts["total"] = sum(counts.values())
    return counts


# ---------------------------------------------------------------- auth & pages


@router.get("/login", response_class=HTMLResponse)
def login_page(request: Request, error: str = "") -> HTMLResponse:
    return templates.TemplateResponse("login.html", {"request": request, "error": error})


@router.post("/login")
def login_action(
    request: Request,
    username: str = Form(""),
    password: str = Form(""),
) -> RedirectResponse:
    from app.auth import (
        SESSION_COOKIE_NAME,
        SESSION_MAX_AGE,
        create_session_token,
        verify_credentials,
    )

    if verify_credentials(username, password):
        token = create_session_token(username)
        response = RedirectResponse(url="/", status_code=303)
        response.set_cookie(
            key=SESSION_COOKIE_NAME,
            value=token,
            max_age=SESSION_MAX_AGE,
            httponly=True,
            samesite="lax",
        )
        return response

    return RedirectResponse(url="/login?error=Invalid+credentials", status_code=303)


@router.get("/logout")
@router.post("/logout")
def logout_action() -> RedirectResponse:
    from app.auth import SESSION_COOKIE_NAME

    response = RedirectResponse(url="/login", status_code=303)
    response.delete_cookie(SESSION_COOKIE_NAME)
    return response


@router.get("/", response_class=HTMLResponse)
def dashboard(
    request: Request,
    session: Session = Depends(get_session),
    min_score: int = 0,
    status: str = "",
    source: str = "",
    q: str = "",
) -> HTMLResponse:
    """Job list, highest score first. Unscored jobs appear after scored ones."""
    stmt = (
        select(Job, JobScore)
        .join(JobScore, JobScore.job_id == Job.id, isouter=True)
        .where(Job.status != JobStatus.EXPIRED)
    )
    if status:
        stmt = stmt.where(Job.status == status)
    if source:
        stmt = stmt.where(Job.source == source)
    if q:
        like = f"%{q}%"
        stmt = stmt.where(Job.title.ilike(like) | Job.company.ilike(like))
    if min_score:
        stmt = stmt.where(JobScore.total >= min_score)

    rows = session.exec(
        stmt.order_by(JobScore.total.desc().nullslast(), Job.first_seen_at.desc())
        .limit(200)
    ).all()

    sources = session.exec(select(Job.source).distinct()).all()

    return templates.TemplateResponse(
        request,
        "dashboard.html",
        {
            "rows": rows,
            "counts": _counts(session),
            "sources": sorted(s for s in sources if s),
            "filters": {
                "min_score": min_score, "status": status, "source": source, "q": q,
            },
            "settings": get_settings(),
            "profile": _profile(session),
        },
    )


@router.get("/job/{job_id}", response_class=HTMLResponse)
def job_detail(
    job_id: int, request: Request, session: Session = Depends(get_session)
) -> HTMLResponse:
    job = session.get(Job, job_id)
    if job is None:
        raise HTTPException(404, "job not found")

    score = session.exec(select(JobScore).where(JobScore.job_id == job_id)).first()
    app_row = session.exec(
        select(Application).where(Application.job_id == job_id)
    ).first()
    version = (
        session.get(ResumeVersion, app_row.resume_version_id)
        if app_row and app_row.resume_version_id else None
    )
    prep = session.exec(
        select(InterviewPrep).where(InterviewPrep.job_id == job_id)
    ).first()

    return templates.TemplateResponse(
        request,
        "job_detail.html",
        {
            "job": job, "score": score, "application": app_row,
            "version": version, "prep": prep,
            "settings": get_settings(), "profile": _profile(session),
        },
    )


@router.get("/review", response_class=HTMLResponse)
def review_queue(
    request: Request, session: Session = Depends(get_session)
) -> HTMLResponse:
    """Prepared applications awaiting a human decision."""
    rows = session.exec(
        select(Application, Job, JobScore)
        .join(Job, Job.id == Application.job_id)
        .join(JobScore, JobScore.job_id == Job.id, isouter=True)
        .where(Application.submitted_at.is_(None))
        .order_by(JobScore.total.desc().nullslast())
    ).all()

    return templates.TemplateResponse(
        request,
        "review.html",
        {
            "rows": rows, "counts": _counts(session),
            "settings": get_settings(), "profile": _profile(session),
        },
    )


@router.get("/applications", response_class=HTMLResponse)
def applications(
    request: Request, session: Session = Depends(get_session)
) -> HTMLResponse:
    rows = session.exec(
        select(Application, Job)
        .join(Job, Job.id == Application.job_id)
        .where(Application.submitted_at.is_not(None))
        .order_by(Application.submitted_at.desc())
    ).all()

    return templates.TemplateResponse(
        request,
        "applications.html",
        {
            "rows": rows, "counts": _counts(session),
            "settings": get_settings(), "profile": _profile(session),
        },
    )


@router.get("/profile", response_class=HTMLResponse)
def profile_page(
    request: Request, session: Session = Depends(get_session)
) -> HTMLResponse:
    return templates.TemplateResponse(
        request,
        "profile.html",
        {
            "profile": _profile(session), "counts": _counts(session),
            "settings": get_settings(),
        },
    )


@router.get("/sources", response_class=HTMLResponse)
def sources_page(
    request: Request, session: Session = Depends(get_session)
) -> HTMLResponse:
    """Connector health. Surfaces a source that broke quietly."""
    from app.models import SourceRun

    latest = session.exec(
        select(SourceRun).order_by(SourceRun.started_at.desc()).limit(120)
    ).all()

    by_source: dict[str, dict] = {}
    for run in latest:
        entry = by_source.setdefault(run.source, {
            "source": run.source, "last": run, "runs": 0,
            "failures": 0, "found": 0, "new": 0,
        })
        entry["runs"] += 1
        entry["found"] += run.found
        entry["new"] += run.new_jobs
        if not run.ok:
            entry["failures"] += 1

    return templates.TemplateResponse(
        request,
        "sources.html",
        {
            "sources": sorted(by_source.values(), key=lambda e: e["source"]),
            "recent": latest[:40],
            "counts": _counts(session),
            "settings": get_settings(),
            "profile": _profile(session),
        },
    )


@router.get("/health")
def health(session: Session = Depends(get_session)) -> dict:
    from app import scheduler as sched

    scheduler = sched.get_scheduler()
    return {
        "ok": True,
        "jobs": _counts(session),
        "scheduler_running": bool(scheduler and scheduler.running),
        "scheduled_jobs": [j.id for j in scheduler.get_jobs()] if scheduler else [],
        "llm_enabled": get_settings().llm_enabled,
        "auto_submit": get_settings().enable_auto_submit,
        "warnings": get_settings().startup_warnings(),
    }


# ---------------------------------------------------------------- actions


@router.post("/profile")
async def save_profile(
    request: Request,
    session: Session = Depends(get_session),
    full_name: str = Form(""),
    email: str = Form(""),
    phone: str = Form(""),
    location: str = Form(""),
    linkedin_url: str = Form(""),
    github_username: str = Form(""),
    portfolio_url: str = Form(""),
    min_salary: str = Form(""),
    salary_currency: str = Form("USD"),
    target_titles: str = Form(""),
    target_locations: str = Form(""),
    excluded_companies: str = Form(""),
    work_authorization: str = Form(""),
    years_experience: str = Form(""),
    remote_only: str = Form(""),
) -> RedirectResponse:
    profile = _profile(session) or Profile()

    profile.full_name = full_name.strip()
    profile.email = email.strip()
    profile.phone = phone.strip()
    profile.location = location.strip()
    profile.linkedin_url = linkedin_url.strip()
    profile.github_username = github_username.strip().removeprefix("@")
    profile.portfolio_url = portfolio_url.strip()
    profile.salary_currency = salary_currency.strip() or "USD"
    profile.work_authorization = work_authorization.strip()
    profile.remote_only = remote_only.lower() in ("on", "true", "yes", "1")
    profile.min_salary = _int_or_none(min_salary)
    profile.years_experience = _int_or_none(years_experience)
    profile.target_titles = _csv(target_titles)
    profile.target_locations = _csv(target_locations)
    profile.excluded_companies = _csv(excluded_companies)

    from app.models import utcnow

    profile.updated_at = utcnow()
    session.add(profile)
    session.commit()
    return RedirectResponse("/profile?saved=1", status_code=303)


@router.post("/profile/resume")
async def upload_resume(
    request: Request, session: Session = Depends(get_session)
) -> RedirectResponse:
    """Store the uploaded resume, extract its text, and run the ATS check."""
    from app.pipeline.keywords import extract_skills
    from app.resume.parse import SUPPORTED, parse_resume

    form = await request.form()
    upload = form.get("resume")
    if upload is None or not getattr(upload, "filename", ""):
        return RedirectResponse("/profile?error=no+file", status_code=303)

    settings = get_settings()
    settings.ensure_dirs()

    from pathlib import Path

    suffix = Path(upload.filename).suffix.lower()
    if suffix not in SUPPORTED:
        return RedirectResponse(
            f"/profile?error=unsupported+type+{suffix}", status_code=303
        )

    dest = settings.resume_dir / f"base{suffix}"
    dest.write_bytes(await upload.read())

    try:
        parsed = parse_resume(dest)
    except Exception as exc:  # noqa: BLE001
        log.exception("resume parse failed")
        return RedirectResponse(f"/profile?error={type(exc).__name__}", status_code=303)

    profile = _profile(session) or Profile()
    profile.base_resume_path = str(dest)
    profile.base_resume_text = parsed.text
    profile.skills = sorted(extract_skills(parsed.text))
    if not profile.email and parsed.emails:
        profile.email = parsed.emails[0]
    if not profile.phone and parsed.phones:
        profile.phone = parsed.phones[0]

    session.add(profile)
    session.commit()
    log.info("resume uploaded: %d words, %d skills", parsed.word_count, len(profile.skills))
    return RedirectResponse("/profile?uploaded=1", status_code=303)


@router.post("/job/{job_id}/tailor")
async def tailor_job(
    job_id: int, session: Session = Depends(get_session)
) -> RedirectResponse:
    from app.apply.runner import prepare

    job = session.get(Job, job_id)
    profile = _profile(session)
    if job is None or profile is None:
        raise HTTPException(404, "job or profile not found")

    try:
        await prepare(session, job, profile)
    except Exception as exc:  # noqa: BLE001
        log.exception("tailoring job %s failed", job_id)
        return RedirectResponse(f"/job/{job_id}?error={type(exc).__name__}", status_code=303)
    return RedirectResponse(f"/job/{job_id}?tailored=1", status_code=303)


@router.post("/job/{job_id}/submit")
async def submit_job(
    job_id: int,
    session: Session = Depends(get_session),
    force: str = Form(""),
) -> RedirectResponse:
    """Submit an application. `force` is the user's explicit approval.

    It bypasses the score and auto-submit-flag gates, but never the truthcheck
    gate — an unverified resume still cannot be sent from here.
    """
    from app.apply.runner import submit

    job = session.get(Job, job_id)
    profile = _profile(session)
    if job is None or profile is None:
        raise HTTPException(404, "job or profile not found")

    result = await submit(
        session, job, profile, force=force.lower() in ("on", "true", "yes", "1")
    )
    flag = "submitted=1" if result.ok else f"error={result.error[:120]}"
    return RedirectResponse(f"/job/{job_id}?{flag}", status_code=303)


@router.post("/job/{job_id}/skip")
def skip_job(job_id: int, session: Session = Depends(get_session)) -> RedirectResponse:
    job = session.get(Job, job_id)
    if job is None:
        raise HTTPException(404, "job not found")
    job.status = JobStatus.SKIPPED
    session.add(job)
    session.commit()
    return RedirectResponse("/", status_code=303)


@router.post("/job/{job_id}/prep")
async def make_prep(
    job_id: int, session: Session = Depends(get_session)
) -> RedirectResponse:
    from app.pipeline.interview import prep_for_job

    job = session.get(Job, job_id)
    profile = _profile(session)
    if job is None or profile is None:
        raise HTTPException(404, "job or profile not found")

    try:
        await prep_for_job(session, job, profile)
    except Exception as exc:  # noqa: BLE001
        log.exception("interview prep for job %s failed", job_id)
        return RedirectResponse(f"/job/{job_id}?error={type(exc).__name__}", status_code=303)
    return RedirectResponse(f"/job/{job_id}?prepped=1", status_code=303)


@router.post("/actions/poll")
async def trigger_poll() -> RedirectResponse:
    """Run a poll immediately rather than waiting for the scheduler."""
    from app.pipeline.ingest import poll_broad, poll_curated

    await poll_curated()
    await poll_broad()
    return RedirectResponse("/sources", status_code=303)


@router.post("/actions/score")
async def trigger_score(session: Session = Depends(get_session)) -> RedirectResponse:
    from app.pipeline.score import score_pending

    await score_pending(session, limit=50)
    return RedirectResponse("/", status_code=303)


@router.post("/actions/github")
async def trigger_github(session: Session = Depends(get_session)) -> RedirectResponse:
    from app.models import utcnow
    from app.pipeline.github import scan

    profile = _profile(session)
    if profile is None or not profile.github_username:
        return RedirectResponse("/profile?error=no+github+username", status_code=303)

    evidence = await scan(profile.github_username)
    if not evidence.ok:
        return RedirectResponse(f"/profile?error={evidence.error[:100]}", status_code=303)

    profile.github_evidence = evidence.to_dict()
    profile.github_synced_at = utcnow()
    session.add(profile)
    session.commit()
    return RedirectResponse("/profile?github=1", status_code=303)


# ---------------------------------------------------------------- helpers


def _csv(value: str) -> list[str]:
    return [part.strip() for part in (value or "").split(",") if part.strip()]


def _int_or_none(value: str) -> int | None:
    digits = "".join(ch for ch in (value or "") if ch.isdigit())
    return int(digits) if digits else None
