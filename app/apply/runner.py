"""Application orchestration.

`prepare` builds everything needed to apply and stops. `submit` is the only
function that can send, and it always consults `SubmitGate` first.

Splitting the two matters: preparation is safe to run automatically on every good
match (that is what wins the 5-minute race), while sending stays behind an
explicit gate.
"""

from __future__ import annotations

import json
import logging

from sqlmodel import Session, select

from app.apply.base import BaseSubmitter, SubmitGate, SubmitResult, get_submitter
from app.apply.email_smtp import EmailSubmitter, application_email
from app.apply.manual import ManualSubmitter, build_form_answers
from app.deps import user_job
from app.llm.client import LlmClient, get_llm
from app.llm.prompts import COVER_LETTER_SYSTEM, build_cover_letter_user
from app.models import (
    Application,
    ApplyMethod,
    Job,
    JobStatus,
    Profile,
    ResumeVersion,
    UserJob,
    utcnow,
)
from app.pipeline.lettercheck import check_letter
from app.pipeline.requirements import Requirements, extract_requirements
from app.pipeline.tailor import TailorError, tailor_for_job
from app.resume.render import render_for_job

log = logging.getLogger(__name__)


async def prepare(
    session: Session,
    job: Job,
    profile: Profile,
    *,
    write_files: bool = True,
    user_id: int,
) -> Application:
    """Build the tailored resume, cover letter and form answers. Sends nothing.

    Idempotent: an already-prepared job returns its existing Application.
    """
    existing = session.exec(
        select(Application).where(
            Application.job_id == job.id, Application.user_id == user_id
        )
    ).first()
    if existing is not None and existing.submitted_at is not None:
        return existing

    version: ResumeVersion | None = None
    try:
        version = await tailor_for_job(session, job, profile, user_id=user_id)
    except TailorError as exc:
        log.warning("job %s: tailoring unavailable (%s); using base resume", job.id, exc)

    if version is not None and write_files and not version.docx_path:
        try:
            docx_path, pdf_path, preserved, note = render_for_job(
                version.text,
                company=job.company,
                title=job.title,
                full_name=profile.full_name,
                profile=profile,
            )
            version.docx_path = str(docx_path)
            version.pdf_path = str(pdf_path) if str(pdf_path) != "." else ""
            version.format_preserved = preserved
            version.template_note = note
            session.add(version)
        except Exception as exc:  # noqa: BLE001
            log.exception("job %s: resume render failed", job.id)
            version.truthcheck_notes = [
                *version.truthcheck_notes, f"render failed: {exc}"
            ]
            session.add(version)

    cover, letter_check = await _cover_letter(job, profile, version)

    app = existing or Application(job_id=job.id, user_id=user_id)
    app.resume_version_id = version.id if version is not None else None
    app.cover_letter = cover
    app.cover_letter_check = letter_check
    app.form_answers = build_form_answers(job, profile)
    app.method = ApplyMethod.MANUAL
    app.queued_at = utcnow()

    state = user_job(session, user_id, job.id)
    state.status = JobStatus.QUEUED
    session.add(app)
    session.add(state)
    session.commit()
    session.refresh(app)

    log.info("prepared application for job %s (%s at %s)", job.id, job.title, job.company)
    return app


async def submit(
    session: Session,
    job: Job,
    profile: Profile,
    *,
    force: bool = False,
    user_id: int,
) -> SubmitResult:
    """Attempt submission. Every gate is evaluated before any network call."""
    app = session.exec(
        select(Application).where(
            Application.job_id == job.id, Application.user_id == user_id
        )
    ).first()
    if app is None:
        app = await prepare(session, job, profile, user_id=user_id)

    version = (
        session.get(ResumeVersion, app.resume_version_id)
        if app.resume_version_id else None
    )

    gate = SubmitGate.check(session, job, version, force=force, user_id=user_id, profile=profile)
    if not gate.allowed:
        log.info("job %s: submission blocked — %s", job.id, gate.reason)
        app.error = gate.reason
        session.add(app)
        session.commit()
        return SubmitResult(
            ok=False, method=ApplyMethod.MANUAL, error=gate.reason
        )

    submitter: BaseSubmitter = get_submitter(job.ats_platform)
    # Plenty of postings accept applications by email rather than through an
    # ATS. When no platform-specific submitter exists and the posting states an
    # address, send from the user's own mailbox instead of parking the job in
    # the manual queue. Also use EmailSubmitter if the user manually specified
    # an application email for this job.
    if isinstance(submitter, ManualSubmitter) and (application_email(job) or app.apply_email):
        submitter = EmailSubmitter()
    dry_run = gate.dry_run or not submitter.can_auto_submit

    app.attempts += 1
    result = await submitter.submit(
        job, profile, version, cover_letter=app.cover_letter, dry_run=dry_run,
        application_email_override=app.apply_email,
    )

    app.method = result.method
    app.confirmation = result.confirmation
    app.error = result.error

    state = user_job(session, user_id, job.id)
    if result.ok and not result.dry_run:
        app.submitted_at = utcnow()
        state.status = JobStatus.APPLIED
        log.info("job %s: APPLIED via %s", job.id, result.method)
    elif result.ok:
        state.status = JobStatus.QUEUED
        log.info("job %s: queued (%s)", job.id, result.confirmation)
    else:
        state.status = JobStatus.FAILED
        log.warning("job %s: submission failed — %s", job.id, result.error)

    session.add(app)
    session.add(state)
    session.commit()
    return result


async def _cover_letter(
    job: Job, profile: Profile, version: ResumeVersion | None
) -> tuple[str, dict]:
    """Draft the letter, verify it, and revise once if the gate blocks it.

    The posting's asks are passed into the prompt instead of being left for the
    model to notice, and a blocked draft goes back once with the findings
    attached. One revision only: the gate still decides what may be sent, and a
    retry loop that can argue with a deterministic check only burns tokens.
    """
    llm = get_llm()
    if not llm.available:
        return "", {}

    resume_text = (version.text if version else profile.base_resume_text) or ""
    if not resume_text.strip():
        return "", {}

    requirements = extract_requirements(job.description or "")
    try:
        cover = await _write_letter(llm, job, resume_text, requirements)
    except Exception as exc:  # noqa: BLE001
        log.warning("job %s: cover letter generation failed: %s", job.id, exc)
        return "", {}

    check = _check_letter(job, profile, version, cover)
    if not _blocked(check):
        return cover, check

    revise = [
        finding["message"]
        for finding in check.get("findings", [])
        if finding.get("severity") == "block"
    ]
    try:
        revised = await _write_letter(
            llm, job, resume_text, requirements, revise=revise, previous=cover
        )
    except Exception as exc:  # noqa: BLE001
        log.warning("job %s: cover letter revision failed: %s", job.id, exc)
        return cover, check

    revised_check = _check_letter(job, profile, version, revised)
    if _blocked(revised_check) and (
        revised_check.get("score", 0.0) < check.get("score", 0.0)
    ):
        # A failed retry must never replace the better draft.
        return cover, check
    return revised, revised_check


async def _write_letter(
    llm: LlmClient,
    job: Job,
    resume_text: str,
    requirements: Requirements,
    *,
    revise: list[str] | None = None,
    previous: str = "",
) -> str:
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
                requirements=requirements,
                revise=revise or [],
                previous=previous,
            ),
        }],
        max_tokens=900,
        temperature=0.2 if revise else 0.4,
    )
    return result.text.strip()


def _blocked(check: dict) -> bool:
    return bool(check) and check.get("passed") is False


def _check_letter(
    job: Job, profile: Profile, version: ResumeVersion | None, cover: str
) -> dict:
    """Gate the letter the way truthcheck gates the CV.

    Fails closed: a checker error is recorded as a block, never as a pass. An
    empty letter returns no verdict at all — the posting may not have asked for
    one, and the submit gate only consults a verdict that exists.
    """
    if not cover.strip():
        return {}

    resume_text = (
        version.text if version is not None else profile.base_resume_text
    ) or ""
    evidence = (
        json.dumps(profile.github_evidence, ensure_ascii=False)
        if profile.github_evidence else ""
    )
    try:
        return check_letter(
            cover,
            job_description=job.description or "",
            resume_text=resume_text,
            github_evidence=evidence,
            company=job.company or "",
        ).as_dict()
    except Exception:  # noqa: BLE001 - an error must not read as approval
        log.exception("job %s: cover letter check failed", job.id)
        return {
            "passed": False,
            "score": 0.0,
            "word_count": len(cover.split()),
            "requirements": {},
            "findings": [{
                "rule": "checker_error", "severity": "block",
                "message": "Letter verification did not complete.",
                "fix": "Read the letter once before sending.",
            }],
        }


async def prepare_queue(session: Session, user_id: int, limit: int = 10) -> int:
    """Prepare artifacts for one user's high-scoring, not-yet-queued jobs.

    This is the step that makes fast application possible: by the time the user
    looks, the paperwork is already done.
    """
    from app.models import JobScore

    profile = session.exec(
        select(Profile).where(Profile.user_id == user_id)
    ).first()
    if profile is None:
        log.warning("user %s has no profile; skipping queue preparation", user_id)
        return 0

    threshold = profile.alert_min_score - 15
    rows = session.exec(
        select(Job, JobScore)
        .join(
            JobScore,
            (JobScore.job_id == Job.id) & (JobScore.user_id == user_id),
        )
        .join(
            UserJob,
            (UserJob.job_id == Job.id) & (UserJob.user_id == user_id),
        )
        .where(UserJob.status == JobStatus.SCORED)
        .where(JobScore.total >= threshold)
        .order_by(JobScore.total.desc())
        .limit(limit)
    ).all()

    prepared = 0
    for job, _score in rows:
        try:
            await prepare(session, job, profile, user_id=user_id)
            prepared += 1
        except Exception:  # noqa: BLE001
            log.exception("failed preparing job %s", job.id)
    return prepared
