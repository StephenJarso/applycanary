"""Interview preparation.

Generated from the job description plus the user's own resume, so questions are
about work they actually did rather than generic lists. Skill gaps come from
requirements with no supporting evidence — the honest answer to "what should I
study before this interview".
"""

from __future__ import annotations

import logging

from sqlmodel import Session, select

from app.config import get_settings
from app.llm.client import get_llm
from app.llm.prompts import INTERVIEW_SYSTEM, build_interview_user
from app.models import InterviewPrep, Job, Profile, utcnow
from app.pipeline.keywords import keyword_overlap

log = logging.getLogger(__name__)


class InterviewPrepError(RuntimeError):
    pass


async def prep_for_job(
    session: Session, job: Job, profile: Profile, *, force: bool = False
) -> InterviewPrep:
    existing = session.exec(
        select(InterviewPrep).where(InterviewPrep.job_id == job.id)
    ).first()
    if existing is not None and not force:
        return existing

    llm = get_llm()
    if not llm.available:
        raise InterviewPrepError("ANTHROPIC_API_KEY is not configured")

    resume_text = (profile.base_resume_text or "").strip()
    if not resume_text:
        raise InterviewPrepError("no base resume on file")

    _, _, missing = keyword_overlap(resume_text, job.description or "")

    settings = get_settings()
    try:
        parsed, result = await llm.complete_json(
            model=settings.model_tailor,
            system=INTERVIEW_SYSTEM,
            messages=[{
                "role": "user",
                "content": build_interview_user(
                    title=job.title,
                    company=job.company,
                    description=job.description or "",
                    resume_text=resume_text,
                    gaps=missing[:12],
                ),
            }],
            max_tokens=3000,
            temperature=0.3,
        )
    except Exception as exc:  # noqa: BLE001
        raise InterviewPrepError(f"interview prep call failed: {exc}") from exc

    prep = existing or InterviewPrep(job_id=job.id)
    prep.technical_questions = _questions(parsed.get("technical_questions"))
    prep.behavioural_questions = _questions(parsed.get("behavioural_questions"))
    prep.questions_to_ask = [
        str(q) for q in (parsed.get("questions_to_ask") or []) if str(q).strip()
    ][:8]
    prep.company_notes = str(parsed.get("company_notes") or "")
    # Prefer the model's reading of the gaps, fall back to the keyword diff.
    prep.skill_gaps = [
        str(g) for g in (parsed.get("skill_gaps") or missing[:8]) if str(g).strip()
    ][:10]
    prep.model_used = result.model
    prep.created_at = utcnow()

    session.add(prep)
    session.commit()
    session.refresh(prep)

    log.info(
        "interview prep for job %s: %d technical, %d behavioural, %d gaps",
        job.id, len(prep.technical_questions), len(prep.behavioural_questions),
        len(prep.skill_gaps),
    )
    return prep


def _questions(raw: object) -> list[dict]:
    """Normalise to [{question, answer, why}] and drop malformed entries."""
    if not isinstance(raw, list):
        return []
    out: list[dict] = []
    for item in raw[:12]:
        if isinstance(item, str) and item.strip():
            out.append({"question": item.strip(), "answer": "", "why": ""})
        elif isinstance(item, dict):
            question = str(item.get("question") or "").strip()
            if not question:
                continue
            out.append({
                "question": question,
                "answer": str(item.get("answer") or item.get("suggested_answer") or ""),
                "why": str(item.get("why") or item.get("rationale") or ""),
            })
    return out
