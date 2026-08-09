"""Application orchestration.

`prepare` builds everything needed to apply and stops. `submit` is the only
function that can send, and it always consults `SubmitGate` first.

Splitting the two matters: preparation is safe to run automatically on every good
match (that is what wins the 5-minute race), while sending stays behind an
explicit gate.
"""

from __future__ import annotations

import logging

from sqlmodel import Session, select

from app.apply.base import BaseSubmitter, SubmitGate, SubmitResult, get_submitter
from app.apply.manual import build_form_answers
from app.config import get_settings
from app.llm.client import get_llm
from app.llm.prompts import COVER_LETTER_SYSTEM, build_cover_letter_user
from app.models import (
    Application,
    ApplyMethod,
    Job,
    JobStatus,
    Profile,
    ResumeVersion,
    utcnow,
)
from app.pipeline.tailor import TailorError, tailor_for_job
from app.resume.render import render_for_job

log = logging.getLogger(__name__)


async def prepare(
    session: Session, job: Job, profile: Profile, *, write_files: bool = True
) -> Application:
    """Build the tailored resume, cover letter and form answers. Sends nothing.

    Idempotent: an already-prepared job returns its existing Application.
    """
    existing = session.exec(
        select(Application).where(Application.job_id == job.id)
    ).first()
    if existing is not None and existing.submitted_at is not None:
        return existing

    version: ResumeVersion | None = None
    try:
        version = await tailor_for_job(session, job, profile)
    except TailorError as exc:
        log.warning("job %s: tailoring unavailable (%s); using base resume", job.id, exc)

    if version is not None and write_files and not version.docx_path:
        try:
            docx_path, pdf_path = render_for_job(
                version.text,
                company=job.company,
                title=job.title,
                full_name=profile.full_name,
            )
            version.docx_path = str(docx_path)
            version.pdf_path = str(pdf_path) if str(pdf_path) != "." else ""
            session.add(version)
        except Exception as exc:  # noqa: BLE001
            log.exception("job %s: resume render failed", job.id)
            version.truthcheck_notes = [
                *version.truthcheck_notes, f"render failed: {exc}"
            ]
            session.add(version)

    cover = await _cover_letter(job, profile, version)

    app = existing or Application(job_id=job.id)
    app.resume_version_id = version.id if version is not None else None
    app.cover_letter = cover
    app.form_answers = build_form_answers(job, profile)
    app.method = ApplyMethod.MANUAL
    app.queued_at = utcnow()

    job.status = JobStatus.QUEUED
    session.add(app)
    session.add(job)
    session.commit()
    session.refresh(app)

    log.info("prepared application for job %s (%s at %s)", job.id, job.title, job.company)
    return app


async def submit(
    session: Session, job: Job, profile: Profile, *, force: bool = False
) -> SubmitResult:
    """Attempt submission. Every gate is evaluated before any network call."""
    app = session.exec(select(Application).where(Application.job_id == job.id)).first()
    if app is None:
        app = await prepare(session, job, profile)

    version = (
        session.get(ResumeVersion, app.resume_version_id)
        if app.resume_version_id else None
    )

    gate = SubmitGate.check(session, job, version, force=force)
    if not gate.allowed:
        log.info("job %s: submission blocked — %s", job.id, gate.reason)
        app.error = gate.reason
        session.add(app)
        session.commit()
        return SubmitResult(
            ok=False, method=ApplyMethod.MANUAL, error=gate.reason
        )

    submitter: BaseSubmitter = get_submitter(job.ats_platform)
    dry_run = gate.dry_run or not submitter.can_auto_submit

    app.attempts += 1
    result = await submitter.submit(
        job, profile, version, cover_letter=app.cover_letter, dry_run=dry_run
    )

    app.method = result.method
    app.confirmation = result.confirmation
    app.error = result.error

    if result.ok and not result.dry_run:
        app.submitted_at = utcnow()
        job.status = JobStatus.APPLIED
        log.info("job %s: APPLIED via %s", job.id, result.method)
    elif result.ok:
        job.status = JobStatus.QUEUED
        log.info("job %s: queued (%s)", job.id, result.confirmation)
    else:
        job.status = JobStatus.FAILED
        log.warning("job %s: submission failed — %s", job.id, result.error)

    session.add(app)
    session.add(job)
    session.commit()
    return result


async def _cover_letter(
    job: Job, profile: Profile, version: ResumeVersion | None
) -> str:
    llm = get_llm()
    if not llm.available:
        return ""

    resume_text = (version.text if version else profile.base_resume_text) or ""
    if not resume_text.strip():
        return ""

    try:
        result = await llm.complete(
            model=llm.tailor_model,
            system=COVER_LETTER_SYSTEM,
            messages=[{
                "role": "user",
                "content": build_cover_letter_user(
                    title=job.title,
                    company=job.company,
                    description=job.description or "",
                    resume_text=resume_text,
                ),
            }],
            max_tokens=900,
            temperature=0.4,
        )
        return result.text.strip()
    except Exception as exc:  # noqa: BLE001
        log.warning("job %s: cover letter generation failed: %s", job.id, exc)
        return ""


async def prepare_queue(session: Session, limit: int = 10) -> int:
    """Prepare artifacts for high-scoring jobs that are scored but not yet queued.

    This is the step that makes fast application possible: by the time the user
    looks, the paperwork is already done.
    """
    from app.models import JobScore

    profile = session.exec(select(Profile)).first()
    if profile is None:
        log.warning("no profile configured; skipping queue preparation")
        return 0

    settings = get_settings()
    rows = session.exec(
        select(Job, JobScore)
        .join(JobScore, JobScore.job_id == Job.id)
        .where(Job.status == JobStatus.SCORED)
        .where(JobScore.total >= settings.alert_min_score - 15)
        .order_by(JobScore.total.desc())
        .limit(limit)
    ).all()

    prepared = 0
    for job, _score in rows:
        try:
            await prepare(session, job, profile)
            prepared += 1
        except Exception:  # noqa: BLE001
            log.exception("failed preparing job %s", job.id)
    return prepared
