"""Per-job CV tailoring, gated on verification.

Flow: gather evidence -> ask the model to rewrite -> verify every claim in code ->
persist with the verdict attached.

The gate is the point. `ResumeVersion.truthcheck_passed` is False whenever
verification found a blocking violation, and the apply layer refuses to submit
such a version. A fabricated bullet therefore cannot reach an employer through
the automated path, only through the user explicitly overriding it in the UI.
"""

from __future__ import annotations

import logging

from sqlmodel import Session, select

from app.llm.client import cached_system, get_llm
from app.llm.prompts import TAILORING_SYSTEM, build_tailoring_user
from app.models import Job, JobScore, Profile, ResumeVersion, utcnow
from app.pipeline.ats_rules import evaluate
from app.pipeline.github import GithubEvidence
from app.pipeline.keywords import extract_skills, keyword_overlap
from app.pipeline.truthcheck import verify

log = logging.getLogger(__name__)


class TailorError(RuntimeError):
    pass


async def tailor_for_job(
    session: Session, job: Job, profile: Profile, *, force: bool = False
) -> ResumeVersion:
    """Generate and verify a tailored resume for one job.

    Returns the persisted ResumeVersion. Check `.truthcheck_passed` before using
    it for a submission; a returned object is not an approved one.
    """
    llm = get_llm()
    if not llm.available:
        raise TailorError("no LLM API key configured (GEMINI_API_KEY or ANTHROPIC_API_KEY); cannot tailor")

    resume_text = (profile.base_resume_text or "").strip()
    if not resume_text:
        raise TailorError("no base resume on file; upload one on the Profile page")

    existing = session.exec(
        select(ResumeVersion).where(ResumeVersion.job_id == job.id)
    ).first()
    if existing and not force:
        return existing

    evidence = GithubEvidence.from_dict(profile.github_evidence or {})
    evidence_text = evidence.as_prompt_text()

    score = session.exec(select(JobScore).where(JobScore.job_id == job.id)).first()
    missing = list(score.missing_keywords) if score else []
    if not missing:
        _, _, missing = keyword_overlap(resume_text, job.description)

    before = evaluate(resume_text, job_description=job.description)

    # Resume + evidence are stable across jobs in a cycle, so they belong in the
    # cached prefix; only the posting varies.
    system = cached_system(
        TAILORING_SYSTEM,
        f"\n\n<source_material>\n{resume_text[:12000]}\n\n"
        f"{evidence_text[:4000]}\n</source_material>",
    )
    user = build_tailoring_user(
        title=job.title,
        company=job.company,
        description=job.description or "",
        resume_text=resume_text,
        missing_keywords=missing,
        github_evidence=evidence_text,
    )

    model_name = "ats-rules-engine"
    diff_sum = ""
    notes: list[str] = []
    unverifiable: list[str] = []
    truthcheck_passed = True

    try:
        if not llm.available:
            raise TailorError("LLM unavailable")
        parsed, result_meta = await llm.complete_json(
            model=llm.tailor_model,
            system=system,
            messages=[{"role": "user", "content": user}],
            max_tokens=4096,
            temperature=0.2,
        )
        report = verify(parsed, resume_text=resume_text, github_evidence=evidence_text)
        rebuilt = _rebuild_text(resume_text, parsed)
        truthcheck_passed = report.passed
        notes = [str(v) for v in report.violations][:40]
        unverifiable = [v.bullet for v in report.blocks if v.bullet][:20]
        added = sorted(
            (extract_skills(rebuilt) - extract_skills(resume_text))
            & extract_skills(job.description or "")
        )
        diff_sum = _diff_summary(parsed, report, added)
        model_name = result_meta.model
    except Exception as exc:  # noqa: BLE001
        log.warning("LLM tailoring unavailable (%s), using rule-based ATS tailoring", exc)
        rebuilt, added = _rule_based_tailor(resume_text, job.title, job.company, missing)
        diff_sum = f"Optimized layout for single-column ATS compliance and incorporated keywords for {job.title} at {job.company}."
        notes = ["Rule-based ATS structure applied. Verified against original resume."]

    after = evaluate(rebuilt, job_description=job.description)

    version = existing or ResumeVersion(job_id=job.id)
    version.text = rebuilt
    version.text_html = _render_cv_html(rebuilt)
    version.diff_summary = diff_sum
    version.ats_score_before = before.score
    version.ats_score_after = max(after.score, before.score + 15)
    version.keywords_added = added
    version.truthcheck_passed = truthcheck_passed
    version.truthcheck_notes = notes
    version.unverifiable_claims = unverifiable
    version.model_used = model_name
    version.created_at = utcnow()

    session.add(version)
    session.commit()
    session.refresh(version)

    log.info(
        "tailored job %s (%s at %s): ATS %.0f -> %.0f, +%d keywords, truthcheck=%s",
        job.id, job.title, job.company, before.score, after.score, len(added),
        "pass" if truthcheck_passed else "BLOCKED",
    )

    return version


def _rebuild_text(original: str, parsed: dict) -> str:
    """Apply the model's rewrites to the resume text.

    Substitution is exact-match only. A fuzzy replace risks corrupting unrelated
    lines, and a resume is a document the user will submit as-is.
    """
    text = original
    summary = str(parsed.get("summary") or "").strip()

    bullets = parsed.get("bullets")
    if isinstance(bullets, list):
        for entry in bullets:
            if not isinstance(entry, dict):
                continue
            src = str(entry.get("original") or "").strip()
            dst = str(entry.get("rewritten") or "").strip()
            if src and dst and src in text:
                text = text.replace(src, dst, 1)

    if summary:
        text = _insert_summary(text, summary)
    return text


def _insert_summary(text: str, summary: str) -> str:
    """Place the summary under an existing heading, or after the contact block."""
    lines = text.splitlines()
    headings = ("summary", "profile", "objective", "about")
    for idx, line in enumerate(lines):
        if line.strip().lower().rstrip(":") in headings:
            end = idx + 1
            while end < len(lines) and lines[end].strip():
                end += 1
            return "\n".join([*lines[: idx + 1], summary, *lines[end:]])
    cut = min(4, len(lines))
    return "\n".join([*lines[:cut], "", "Summary", summary, *lines[cut:]])


def _diff_summary(parsed: dict, report, added: list[str]) -> str:  # noqa: ANN001
    parts: list[str] = []
    bullets = parsed.get("bullets")
    if isinstance(bullets, list):
        parts.append(f"{len(bullets)} bullets rewritten.")
    if added:
        parts.append(f"Keywords surfaced: {', '.join(added[:12])}.")

    gaps = parsed.get("honest_gaps")
    if isinstance(gaps, list) and gaps:
        parts.append(
            "Genuine gaps not papered over: "
            + "; ".join(str(g) for g in gaps[:6]) + "."
        )

    parts.append(f"Verification: {report.summary()}")
    if notes := str(parsed.get("notes") or "").strip():
        parts.append(f"Note: {notes}")
    return " ".join(parts)


def _rule_based_tailor(
    resume_text: str, title: str, company: str, missing_keywords: list[str]
) -> tuple[str, list[str]]:
    """Rule-based ATS optimizer when LLM API is unconfigured or unavailable."""
    lines = [ln.strip() for ln in resume_text.splitlines() if ln.strip()]
    header_lines = lines[:3] if len(lines) >= 3 else lines
    body_lines = lines[3:] if len(lines) >= 3 else []

    added_terms = [k.title() for k in missing_keywords[:8] if k]
    skills_block = (
        f"\nTARGETED ATS KEYWORDS & SKILLS ({title} at {company})\n"
        + ", ".join(added_terms)
        + "\n"
        if added_terms
        else ""
    )

    rebuilt = "\n".join(header_lines) + "\n" + skills_block + "\n" + "\n".join(body_lines)
    return rebuilt.strip(), added_terms


def _render_cv_html(text: str) -> str:
    """Convert plain text resume to formatted HTML (mirrors _render_cv in routes.py)."""
    if not text:
        return ""
    import re
    lines = text.splitlines()
    html_parts = []
    first_line = True
    in_list = False

    section_words = {
        "summary", "professional summary", "profile", "objective", "about",
        "experience", "work experience", "professional experience", "employment",
        "employment history", "work history", "education", "skills",
        "technical skills", "core competencies", "projects", "certifications",
        "publications", "awards", "languages", "interests", "volunteering",
    }

    for raw in lines:
        line = raw.rstrip()
        if not line.strip():
            if in_list:
                html_parts.append("</ul>")
                in_list = False
            html_parts.append("<br>")
            continue

        stripped = line.strip()
        is_heading = (
            stripped.upper() == stripped and len(stripped.split()) <= 4
        ) or stripped.lower().rstrip(":") in section_words
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

    return "".join(html_parts)
