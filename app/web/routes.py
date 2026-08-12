"""Dashboard routes.

Server-rendered Jinja templates, no build step. Actions that mutate state are
POSTs that redirect back, so the browser back button never re-submits an
application.
"""

from __future__ import annotations

import logging
import re

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from markupsafe import Markup
from sqlmodel import Session, func, select

from app.config import get_settings
from app.db import get_session
from app.deps import current_user, user_job
from app.models import (
    Application,
    InterviewPrep,
    InviteCode,
    Job,
    JobScore,
    JobStatus,
    Profile,
    ResumeVersion,
    User,
    UserJob,
    utcnow,
)

log = logging.getLogger(__name__)


def _redirect(path: str) -> RedirectResponse:
    """Build a redirect to the frontend base URL + path."""
    settings = get_settings()
    base = settings.frontend_base_url.rstrip("/")
    return RedirectResponse(url=f"{base}{path}", status_code=303)


def _render_cv(text: str) -> Markup:
    """Convert plain text resume to formatted HTML."""
    if not text:
        return Markup("")

    lines = text.splitlines()
    html_parts = []
    first_line = True
    in_list = False

    for raw in lines:
        line = raw.rstrip()
        if not line.strip():
            if in_list:
                html_parts.append("</ul>")
                in_list = False
            html_parts.append("<br>")
            continue

        stripped = line.strip()

        # Detect section headings (ALL CAPS or known section words)
        section_words = {
            "summary", "professional summary", "profile", "objective", "about",
            "experience", "work experience", "professional experience", "employment",
            "employment history", "work history", "education", "skills",
            "technical skills", "core competencies", "projects", "certifications",
            "publications", "awards", "languages", "interests", "volunteering",
        }
        is_heading = (
            stripped.upper() == stripped and len(stripped.split()) <= 4
        ) or stripped.lower().rstrip(":") in section_words

        # Detect bullet points
        is_bullet = bool(re.match(r"^\s*[-*•‣▪●·]\s+", line))

        if is_heading:
            if in_list:
                html_parts.append("</ul>")
                in_list = False
            if first_line:
                html_parts.append(f'<h1 class="cv-name">{stripped}</h1>')
                first_line = False
            else:
                html_parts.append(f'<h2 class="cv-section">{stripped.rstrip(":").upper()}</h2>')
            continue

        if is_bullet:
            if not in_list:
                html_parts.append("<ul class=\"cv-bullets\">")
                in_list = True
            bullet_text = re.sub(r"^\s*[-*•‣▪●·]\s+", "", stripped)
            html_parts.append(f"<li>{bullet_text}</li>")
            continue

        # Regular paragraph
        if in_list:
            html_parts.append("</ul>")
            in_list = False

        if first_line:
            html_parts.append(f'<h1 class="cv-name">{stripped}</h1>')
            first_line = False
        else:
            html_parts.append(f'<p class="cv-text">{stripped}</p>')

    if in_list:
        html_parts.append("</ul>")

    return Markup("".join(html_parts))


router = APIRouter()
templates = Jinja2Templates(directory=str(get_settings().templates_dir))

# Register custom filter
templates.env.filters["render_cv"] = _render_cv


def _profile(session: Session, user_id: int) -> Profile | None:
    return session.exec(
        select(Profile).where(Profile.user_id == user_id)
    ).first()


def _counts(session: Session, user_id: int) -> dict[str, int]:
    """Status tallies for one user.

    Counts come from `userjob`, not `job`: the postings table is shared, so
    grouping by `job.status` would show every user the same numbers.
    """
    rows = session.exec(
        select(UserJob.status, func.count(UserJob.id))
        .where(UserJob.user_id == user_id)
        .group_by(UserJob.status)
    ).all()
    counts = {str(status): n for status, n in rows}
    counts["total"] = sum(counts.values())
    return counts


# ---------------------------------------------------------------- auth & pages


@router.get("/login", response_class=HTMLResponse)
def login_page(request: Request, error: str = "") -> HTMLResponse:
    return templates.TemplateResponse(request, "login.html", {"error": error})


def _set_session_cookie(response: RedirectResponse, user: User) -> None:
    from app.auth import SESSION_COOKIE_NAME, SESSION_MAX_AGE, create_session_token

    response.set_cookie(
        key=SESSION_COOKIE_NAME,
        value=create_session_token(user),
        max_age=SESSION_MAX_AGE,
        httponly=True,
        samesite="lax",
        # Secure whenever the app is reachable off-box, so the session cookie
        # never travels in clear text. See Settings.session_cookie_secure.
        secure=get_settings().session_cookie_secure,
    )


@router.post("/login")
def login_action(
    request: Request,
    username: str = Form(""),
    password: str = Form(""),
    session: Session = Depends(get_session),
) -> RedirectResponse:
    from app.auth import authenticate

    user = authenticate(session, username, password)
    if user is None:
        # Deliberately vague: naming which half was wrong tells an attacker
        # which email addresses have accounts.
        return _redirect("/login?error=Invalid+credentials")

    user.last_login_at = utcnow()
    session.add(user)
    session.commit()

    response = RedirectResponse(url="/", status_code=303)
    _set_session_cookie(response, user)
    return response


@router.get("/register", response_class=HTMLResponse)
def register_page(request: Request, error: str = "") -> HTMLResponse:
    return templates.TemplateResponse(request, "register.html", {"error": error})


@router.post("/register")
def register_action(
    email: str = Form(""),
    password: str = Form(""),
    invite_code: str = Form(""),
    session: Session = Depends(get_session),
) -> RedirectResponse:
    from app.auth import hash_password

    email = (email or "").strip().lower()
    if not email or not password:
        return _redirect("/register?error=Email+and+password+are+required")
    if len(password) < 10:
        return _redirect("/register?error=Password+must+be+at+least+10+characters")

    invite = session.exec(
        select(InviteCode).where(InviteCode.code == invite_code.strip())
    ).first()
    if invite is None or not invite.is_redeemable():
        return _redirect("/register?error=That+invite+code+is+not+valid")

    if session.exec(select(User).where(User.email == email)).first() is not None:
        return _redirect("/register?error=That+email+is+already+registered")

    user = User(email=email, password_hash=hash_password(password))
    session.add(user)
    session.commit()
    session.refresh(user)

    # Mark the code spent only after the account exists, so a failure above
    # leaves the invite usable rather than burning it.
    invite.used_by_id = user.id
    invite.used_at = utcnow()
    session.add(invite)

    # Every user needs a profile row; create an empty one so the dashboard has
    # something to render before they fill it in.
    session.add(Profile(user_id=user.id, email=email))
    session.commit()

    log.info("registered new user %s", email)
    response = RedirectResponse(url="/profile", status_code=303)
    _set_session_cookie(response, user)
    return response


@router.get("/logout")
@router.post("/logout")
def logout_action() -> RedirectResponse:
    from app.auth import SESSION_COOKIE_NAME

    response = _redirect("/login")
    response.delete_cookie(SESSION_COOKIE_NAME)
    return response


@router.get("/", response_class=HTMLResponse)
def dashboard(
    request: Request,
    session: Session = Depends(get_session),
    user: User = Depends(current_user),
    min_score: int = 0,
    status: str = "",
    source: str = "",
    q: str = "",
) -> HTMLResponse:
    """Job list, highest score first. Unscored jobs appear after scored ones."""
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
    if status == "rejected":
        stmt = stmt.where(UserJob.status == JobStatus.REJECTED)
    elif status:
        stmt = stmt.where(UserJob.status == status)
    else:
        # Relevance filtering (target titles / seniority) runs at tier 1 and marks
        # non-matching jobs REJECTED. Hide them by default so the dashboard shows
        # only jobs worth acting on; pick "rejected" above to audit them. A NULL
        # status means "never seen", which is not rejected.
        stmt = stmt.where(
            (UserJob.status.is_(None)) | (UserJob.status != JobStatus.REJECTED)
        )
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

    from app.sources import all_sources

    distinct_db_sources = set(session.exec(select(Job.source).distinct()).all())
    all_configured = set(all_sources().keys())
    sources = sorted([s for s in (distinct_db_sources | all_configured) if s])

    return templates.TemplateResponse(
        request,
        "dashboard.html",
        {
            "rows": rows,
            "counts": _counts(session, user.id),
            "sources": sources,
            "filters": {
                "min_score": min_score, "status": status, "source": source, "q": q,
            },
            "settings": get_settings(),
            "profile": _profile(session, user.id),
        },
    )


@router.get("/job/{job_id}", response_class=HTMLResponse)
def job_detail(
    job_id: int,
    request: Request,
    session: Session = Depends(get_session),
    user: User = Depends(current_user),
) -> HTMLResponse:
    job = session.get(Job, job_id)
    if job is None:
        raise HTTPException(404, "job not found")

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
        if app_row and app_row.resume_version_id else None
    )
    prep = session.exec(
        select(InterviewPrep).where(
            InterviewPrep.job_id == job_id, InterviewPrep.user_id == user.id
        )
    ).first()

    return templates.TemplateResponse(
        request,
        "job_detail.html",
        {
            "job": job, "score": score, "application": app_row,
            "version": version, "prep": prep,
            "settings": get_settings(), "profile": _profile(session, user.id),
        },
    )


@router.get("/review", response_class=HTMLResponse)
def review_queue(
    request: Request,
    session: Session = Depends(get_session),
    user: User = Depends(current_user),
) -> HTMLResponse:
    """Prepared applications awaiting a human decision."""
    rows = session.exec(
        select(Application, Job, JobScore)
        .join(Job, Job.id == Application.job_id)
        .join(
            JobScore,
            (JobScore.job_id == Job.id) & (JobScore.user_id == user.id),
            isouter=True,
        )
        .where(
            Application.submitted_at.is_(None),
            Application.user_id == user.id,
        )
        .order_by(JobScore.total.desc().nullslast())
    ).all()

    return templates.TemplateResponse(
        request,
        "review.html",
        {
            "rows": rows, "counts": _counts(session, user.id),
            "settings": get_settings(), "profile": _profile(session, user.id),
        },
    )


@router.get("/applications", response_class=HTMLResponse)
def applications(
    request: Request,
    session: Session = Depends(get_session),
    user: User = Depends(current_user),
) -> HTMLResponse:
    rows = session.exec(
        select(Application, Job)
        .join(Job, Job.id == Application.job_id)
        .where(
            Application.submitted_at.is_not(None),
            Application.user_id == user.id,
        )
        .order_by(Application.submitted_at.desc())
    ).all()

    return templates.TemplateResponse(
        request,
        "applications.html",
        {
            "rows": rows, "counts": _counts(session, user.id),
            "settings": get_settings(), "profile": _profile(session, user.id),
        },
    )


@router.get("/profile", response_class=HTMLResponse)
def profile_page(
    request: Request,
    session: Session = Depends(get_session),
    user: User = Depends(current_user),
) -> HTMLResponse:
    return templates.TemplateResponse(
        request,
        "profile.html",
        {
            "profile": _profile(session, user.id),
            "counts": _counts(session, user.id),
            "settings": get_settings(),
        },
    )


@router.get("/sources", response_class=HTMLResponse)
def sources_page(
    request: Request,
    session: Session = Depends(get_session),
    user: User = Depends(current_user),
) -> HTMLResponse:
    """Connector health. Surfaces a source that broke quietly."""
    from app.models import SourceRun
    from app.sources import all_sources

    by_source: dict[str, dict] = {
        name: {
            "source": name, "last": None, "runs": 0,
            "failures": 0, "found": 0, "new": 0,
        }
        for name in all_sources()
    }

    latest = session.exec(
        select(SourceRun).order_by(SourceRun.started_at.desc()).limit(120)
    ).all()

    for run in latest:
        entry = by_source.get(run.source)
        if entry is None:
            entry = {
                "source": run.source, "last": run, "runs": 0,
                "failures": 0, "found": 0, "new": 0,
            }
            by_source[run.source] = entry
        elif entry["last"] is None:
            entry["last"] = run

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
            "counts": _counts(session, user.id),
            "settings": get_settings(),
            "profile": _profile(session, user.id),
        },
    )


@router.get("/health")
def health(session: Session = Depends(get_session)) -> dict:
    """Liveness probe. Unauthenticated, so it exposes no per-user data.

    This is what Fly.io polls. It reports the shared postings count and process
    state only — the per-user status tallies that used to live here were
    readable by anyone who could reach the port.
    """
    from app import scheduler as sched

    scheduler = sched.get_scheduler()
    total_jobs = session.exec(select(func.count(Job.id))).one()
    return {
        "ok": True,
        "jobs": {"total": total_jobs},
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
    user: User = Depends(current_user),
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
    profile = _profile(session, user.id) or Profile(user_id=user.id)

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
    return _redirect("/profile?saved=1")


@router.post("/profile/resume")
async def upload_resume(
    request: Request,
    session: Session = Depends(get_session),
    user: User = Depends(current_user),
) -> RedirectResponse:
    """Store the uploaded resume, extract its text, and run the ATS check."""
    from app.pipeline.keywords import extract_skills
    from app.resume.parse import SUPPORTED, parse_resume

    form = await request.form()
    upload = form.get("resume")
    if upload is None or not getattr(upload, "filename", ""):
        return _redirect("/profile?error=no+file")

    settings = get_settings()
    settings.ensure_dirs()

    from pathlib import Path

    suffix = Path(upload.filename).suffix.lower()
    if suffix not in SUPPORTED:
        return _redirect(f"/profile?error=unsupported+type+{suffix}")

    # Per-user directory: a shared "base.pdf" meant each upload overwrote the
    # previous user's résumé on disk.
    user_dir = settings.resume_dir / f"user_{user.id}"
    user_dir.mkdir(parents=True, exist_ok=True)
    dest = user_dir / f"base{suffix}"
    dest.write_bytes(await upload.read())

    try:
        parsed = parse_resume(dest)
    except Exception as exc:  # noqa: BLE001
        log.exception("resume parse failed")
        return _redirect(f"/profile?error={type(exc).__name__}")

    profile = _profile(session, user.id) or Profile(user_id=user.id)
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
    return _redirect("/profile?uploaded=1")


@router.post("/job/{job_id}/tailor")
async def tailor_job(
    job_id: int,
    session: Session = Depends(get_session),
    user: User = Depends(current_user),
) -> RedirectResponse:
    from app.apply.runner import prepare

    job = session.get(Job, job_id)
    profile = _profile(session, user.id)
    if job is None or profile is None:
        raise HTTPException(404, "job or profile not found")

    try:
        await prepare(session, job, profile, user_id=user.id)
    except Exception as exc:  # noqa: BLE001
        log.exception("tailoring job %s failed", job_id)
        return _redirect(f"/job/{job_id}?error={type(exc).__name__}")
    return _redirect(f"/job/{job_id}?tailored=1")


@router.post("/job/{job_id}/submit")
async def submit_job(
    job_id: int,
    session: Session = Depends(get_session),
    user: User = Depends(current_user),
    force: str = Form(""),
) -> RedirectResponse:
    """Submit an application. `force` is the user's explicit approval.

    It bypasses the score and auto-submit-flag gates, but never the truthcheck
    gate — an unverified resume still cannot be sent from here.
    """
    from app.apply.runner import submit

    job = session.get(Job, job_id)
    profile = _profile(session, user.id)
    if job is None or profile is None:
        raise HTTPException(404, "job or profile not found")

    result = await submit(
        session, job, profile,
        force=force.lower() in ("on", "true", "yes", "1"),
        user_id=user.id,
    )
    flag = "submitted=1" if result.ok else f"error={result.error[:120]}"
    return _redirect(f"/job/{job_id}?{flag}")


@router.post("/job/{job_id}/skip")
def skip_job(
    job_id: int,
    session: Session = Depends(get_session),
    user: User = Depends(current_user),
) -> RedirectResponse:
    job = session.get(Job, job_id)
    if job is None:
        raise HTTPException(404, "job not found")
    row = user_job(session, user.id, job_id)
    row.status = JobStatus.SKIPPED
    row.updated_at = utcnow()
    session.add(row)
    session.commit()
    return _redirect("/")


@router.post("/job/{job_id}/prep")
async def make_prep(
    job_id: int,
    session: Session = Depends(get_session),
    user: User = Depends(current_user),
) -> RedirectResponse:
    from app.pipeline.interview import prep_for_job

    job = session.get(Job, job_id)
    profile = _profile(session, user.id)
    if job is None or profile is None:
        raise HTTPException(404, "job or profile not found")

    try:
        await prep_for_job(session, job, profile, user_id=user.id)
    except Exception as exc:  # noqa: BLE001
        log.exception("interview prep for job %s failed", job_id)
        return _redirect(f"/job/{job_id}?error={type(exc).__name__}")
    return _redirect(f"/job/{job_id}?prepped=1")


@router.post("/actions/poll")
async def trigger_poll() -> RedirectResponse:
    """Run a poll immediately rather than waiting for the scheduler."""
    from app.pipeline.ingest import poll_broad, poll_curated

    await poll_curated()
    await poll_broad()
    return _redirect("/sources")


@router.post("/actions/score")
async def trigger_score(
    session: Session = Depends(get_session),
    user: User = Depends(current_user),
) -> RedirectResponse:
    from app.pipeline.score import score_pending

    await score_pending(session, user_id=user.id, limit=50)
    return _redirect("/")


@router.post("/actions/github")
async def trigger_github(
    session: Session = Depends(get_session),
    user: User = Depends(current_user),
) -> RedirectResponse:
    from app.models import utcnow
    from app.pipeline.github import scan

    profile = _profile(session, user.id)
    if profile is None or not profile.github_username:
        return _redirect("/profile?error=no+github+username")

    evidence = await scan(profile.github_username)
    if not evidence.ok:
        return _redirect(f"/profile?error={evidence.error[:100]}")

    profile.github_evidence = evidence.to_dict()
    profile.github_synced_at = utcnow()
    session.add(profile)
    session.commit()
    return _redirect("/profile?github=1")


# ---------------------------------------------------------------- helpers


def _csv(value: str) -> list[str]:
    return [part.strip() for part in (value or "").split(",") if part.strip()]


def _int_or_none(value: str) -> int | None:
    digits = "".join(ch for ch in (value or "") if ch.isdigit())
    return int(digits) if digits else None
