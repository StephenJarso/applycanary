"""Two-tier job scoring.

Tier 1 is free and local: hard disqualifiers plus keyword coverage. Tier 2 calls
Claude only on what survives. The ordering is the point — polling thousands of
postings a day and sending every one to an LLM would cost far more than the tool
saves, so anything decidable with plain logic is decided that way.

Tier 2 sends the resume in a cached prompt prefix, so the expensive context is
paid for once per cycle rather than once per job.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from sqlmodel import Session, select

from app.llm.client import cached_system, get_llm
from app.llm.prompts import SCORING_SYSTEM, build_scoring_user
from app.models import Job, JobScore, JobStatus, Profile, utcnow
from app.pipeline.ats_rules import evaluate as evaluate_ats
from app.pipeline.keywords import keyword_overlap
from app.pipeline.relevance import relevance_disqualifier
from app.pipeline.tailor import TailorError, tailor_for_job

log = logging.getLogger(__name__)

# Tier-1 gate. Below this, tier 2 is not worth the tokens; the job is rejected
# with its reason recorded so the decision stays inspectable.
TIER1_MIN_COVERAGE = 25.0
# Weights for the blended final score.
W_KEYWORD, W_SEMANTIC = 0.35, 0.65
MAX_JD_CHARS = 6000


@dataclass(slots=True)
class Decision:
    total: float
    verdict: str
    decided_by: str
    keyword_score: float = 0.0
    semantic_score: float = 0.0
    ats_score: float = 0.0
    matched: list[str] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)
    reasoning: str = ""
    disqualifier: str = ""
    model_used: str = ""


# ---------------------------------------------------------------- tier 1


def hard_disqualifier(job: Job, profile: Profile) -> str:
    """Return a reason string if the job is categorically unsuitable, else ""."""
    if profile.excluded_companies:
        excluded = {c.strip().lower() for c in profile.excluded_companies if c.strip()}
        if job.company.strip().lower() in excluded:
            return f"{job.company} is on your excluded list"

    if profile.remote_only and not job.is_remote:
        return "role is not remote and you set remote_only"

    if profile.min_salary and job.salary_max is not None:
        # Never reject on an aggregator's guessed range.
        if not job.salary_is_estimate and job.salary_max < profile.min_salary:
            return (
                f"top of range ({job.salary_max:,} {job.salary_currency}) is below "
                f"your floor ({profile.min_salary:,})"
            )

    if profile.target_locations and not job.is_remote:
        wanted = [loc.strip().lower() for loc in profile.target_locations if loc.strip()]
        if wanted and job.location:
            haystack = job.location.lower()
            if not any(loc in haystack for loc in wanted):
                return f"location {job.location!r} is outside your target locations"

    if not (job.description or "").strip():
        return "posting has no description to evaluate"

    reason = relevance_disqualifier(job, profile)
    if reason:
        return reason

    return ""


def tier1(job: Job, profile: Profile) -> Decision:
    reason = hard_disqualifier(job, profile)
    if reason:
        return Decision(
            total=0.0, verdict="disqualified", decided_by="tier1_filter",
            disqualifier=reason, reasoning=f"Filtered locally: {reason}.",
        )

    resume_text = profile.base_resume_text or " ".join(profile.skills or [])
    coverage, matched, missing = keyword_overlap(resume_text, job.description)

    if coverage < TIER1_MIN_COVERAGE:
        return Decision(
            total=round(coverage, 1), verdict="weak", decided_by="tier1_keyword",
            keyword_score=coverage, matched=matched, missing=missing,
            reasoning=(
                f"Only {coverage:.0f}% of the job's key skills appear in your resume, "
                f"below the {TIER1_MIN_COVERAGE:.0f}% threshold for deeper review."
            ),
        )

    return Decision(
        total=round(coverage, 1), verdict="possible", decided_by="tier1_keyword",
        keyword_score=coverage, matched=matched, missing=missing,
    )


# ---------------------------------------------------------------- tier 2


async def tier2(job: Job, profile: Profile, base: Decision) -> Decision:
    """LLM fit assessment. Falls back to the tier-1 decision on any failure."""
    llm = get_llm()
    if not llm.available:
        return base

    system = cached_system(
        SCORING_SYSTEM,
        # Resume in the cached prefix: identical for every job this cycle.
        f"\n\n<candidate_profile>\n{_profile_block(profile)}\n</candidate_profile>",
    )
    user = build_scoring_user(
        title=job.title,
        company=job.company,
        location=job.location or "unspecified",
        description=(job.description or "")[:MAX_JD_CHARS],
        missing_keywords=base.missing,
    )

    try:
        parsed, result = await llm.complete_json(
            model=llm.triage_model,
            system=system,
            messages=[{"role": "user", "content": user}],
            max_tokens=4096,
        )
    except Exception as exc:  # noqa: BLE001 - scoring must never break ingestion
        log.warning("tier2 scoring failed for job %s: %s", job.id, exc)
        base.reasoning = base.reasoning or "Scored locally; AI review unavailable."
        return base

    semantic = _clamp(parsed.get("fit_score"), base.keyword_score)
    total = round(W_KEYWORD * base.keyword_score + W_SEMANTIC * semantic, 1)

    missing = _str_list(parsed.get("missing_keywords")) or base.missing
    matched = _str_list(parsed.get("matched_keywords")) or base.matched

    verdict = str(parsed.get("verdict") or "").strip().lower()
    if verdict not in ("strong_match", "possible", "weak", "disqualified"):
        verdict = _verdict_from_score(total)

    if result.cache_read_tokens:
        log.debug("tier2 cache hit: %d tokens read from cache", result.cache_read_tokens)

    return Decision(
        total=total, verdict=verdict, decided_by="tier2_llm",
        keyword_score=base.keyword_score, semantic_score=semantic,
        ats_score=base.ats_score,
        matched=matched, missing=missing,
        reasoning=str(parsed.get("reasoning") or "").strip()[:1000],
        model_used=result.model,
    )


# ---------------------------------------------------------------- driver


async def score_job(session: Session, job: Job, profile: Profile) -> Decision:
    """Two-tier scoring plus the ATS structure pass.

    ATS structure is the rule engine's verdict on the resume *as tailored for
    this posting*: Claude first rewrites the resume against the job description,
    then `ats_rules.evaluate` scores the tailored output. When the LLM is absent
    or tailoring fails, the same rules run on the stored base resume so the
    meter is never left at zero.
    """
    decision = tier1(job, profile)
    decision.ats_score = _base_ats_score(profile, job)

    if decision.decided_by == "tier1_filter" or decision.verdict == "weak":
        return decision

    decision = await tier2(job, profile, decision)
    decision.ats_score = await _tailored_ats_score(session, job, profile)
    return decision


def _base_ats_score(profile: Profile, job: Job) -> float:
    resume_text = profile.base_resume_text or " ".join(profile.skills or [])
    return evaluate_ats(resume_text, job_description=job.description).score


async def _tailored_ats_score(
    session: Session, job: Job, profile: Profile
) -> float:
    """Rule-engine score of the Claude-tailored resume, or the base fallback."""
    llm = get_llm()
    if not llm.available:
        return _base_ats_score(profile, job)

    try:
        version = await tailor_for_job(session, job, profile)
    except TailorError as exc:
        log.debug("job %s: tailoring unavailable for ATS pass (%s)", job.id, exc)
        return _base_ats_score(profile, job)

    if version is None or not version.text.strip():
        return _base_ats_score(profile, job)
    # The tailored version already carries the rule-engine verdict on its own
    # rewritten text, so report that rather than re-running the rules.
    return version.ats_score_after


async def score_pending(session: Session, *, limit: int = 25) -> int:
    """Score up to `limit` unscored jobs. Returns how many were scored.

    Newest postings first: a fresh job is worth acting on before a stale one, and
    that ordering is what preserves the fast-apply advantage when a backlog builds.
    """
    profile = session.exec(select(Profile)).first()
    if profile is None:
        log.info("no profile configured; skipping scoring")
        return 0
    if not (profile.base_resume_text or profile.skills):
        log.info("profile has no resume text or skills; skipping scoring")
        return 0

    jobs = session.exec(
        select(Job)
        .where(Job.status == JobStatus.NEW)
        .order_by(Job.posted_at.desc().nullslast(), Job.first_seen_at.desc())
        .limit(limit)
    ).all()

    scored = 0
    for job in jobs:
        try:
            decision = await score_job(session, job, profile)
        except Exception:  # noqa: BLE001
            log.exception("scoring job %s failed", job.id)
            job.status = JobStatus.FAILED
            session.add(job)
            continue

        _persist_decision(session, job, decision)
        scored += 1

    session.commit()
    return scored


def _persist_decision(session: Session, job: Job, decision: Decision) -> None:
    existing = session.exec(select(JobScore).where(JobScore.job_id == job.id)).first()
    score = existing or JobScore(job_id=job.id)

    score.keyword_score = decision.keyword_score
    score.semantic_score = decision.semantic_score
    score.ats_score = decision.ats_score
    score.total = decision.total
    score.matched_keywords = decision.matched
    score.missing_keywords = decision.missing
    score.verdict = decision.verdict
    score.reasoning = decision.reasoning
    score.decided_by = decision.decided_by
    score.disqualifier = decision.disqualifier
    score.model_used = decision.model_used
    score.scored_at = utcnow()

    job.status = (
        JobStatus.REJECTED
        if decision.verdict in ("disqualified", "weak")
        else JobStatus.SCORED
    )

    session.add(score)
    session.add(job)


# ---------------------------------------------------------------- helpers


def _profile_block(profile: Profile) -> str:
    parts = [
        f"Name: {profile.full_name or 'not given'}",
        f"Years of experience: {profile.years_experience if profile.years_experience is not None else 'not given'}",
        f"Work authorization: {profile.work_authorization or 'not given'}",
        f"Stated skills: {', '.join(profile.skills) if profile.skills else 'none recorded'}",
        "",
        "Resume:",
        (profile.base_resume_text or "(no resume text on file)")[:12000],
    ]
    return "\n".join(parts)


def _clamp(value: object, fallback: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return fallback
    return round(max(0.0, min(100.0, float(value))), 1)


def _str_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(v).strip() for v in value if str(v).strip()][:25]


def _verdict_from_score(total: float) -> str:
    if total >= 75:
        return "strong_match"
    if total >= 50:
        return "possible"
    return "weak"
