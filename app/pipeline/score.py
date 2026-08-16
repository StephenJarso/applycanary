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

from app.deps import user_job
from app.llm.client import cached_system, get_llm
from app.llm.prompts import SCORING_SYSTEM, build_scoring_user
from app.models import Job, JobScore, JobStatus, Profile, UserJob, utcnow
from app.pipeline.ats_rules import evaluate as evaluate_ats
from app.pipeline.keywords import keyword_overlap
from app.pipeline.relevance import relevance_disqualifier, title_mismatch_penalty
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

    resume_text = _profile_match_text(profile)
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


async def score_job(
    session: Session, job: Job, profile: Profile, *, user_id: int | None = None
) -> Decision:
    """Two-tier scoring plus the ATS structure pass.

    ATS structure is the rule engine's verdict on the resume *as tailored for
    this posting*: Claude first rewrites the resume against the job description,
    then `ats_rules.evaluate` scores the tailored output. When the LLM is absent
    or tailoring fails, the same rules run on the stored base resume so the
    meter is never left at zero.
    """
    owner = user_id if user_id is not None else profile.user_id
    decision = tier1(job, profile)
    decision.ats_score = _base_ats_score(profile, job)

    if decision.decided_by == "tier1_filter" or decision.verdict == "weak":
        return decision

    decision = await tier2(job, profile, decision)
    decision.ats_score = await _tailored_ats_score(session, job, profile, owner)
    return _apply_title_penalty(decision, job, profile)


def _apply_title_penalty(
    decision: Decision, job: Job, profile: Profile
) -> Decision:
    """Deduct for a title outside ``target_titles`` after the LLM has weighed in.

    Applied here rather than in tier 1 on purpose: deducting before the coverage
    gate would push mismatched titles under the threshold and skip tier 2, which
    is exactly the silent-rejection behaviour this replaced. By the time this
    runs the LLM has already read the full description, so the penalty only
    reorders results that were genuinely considered.
    """
    penalty, reason = title_mismatch_penalty(job, profile)
    if not penalty:
        return decision

    decision.total = round(max(0.0, decision.total - penalty), 1)
    note = reason[0].upper() + reason[1:]
    decision.reasoning = f"{decision.reasoning} {note}.".strip()
    if decision.verdict == "strong_match":
        # A title this far off should not present as a top match, even when the
        # description reads well.
        decision.verdict = "possible"
    return decision


def _base_ats_score(profile: Profile, job: Job) -> float:
    resume_text = profile.base_resume_text or " ".join(profile.skills or [])
    return evaluate_ats(resume_text, job_description=job.description).score


async def _tailored_ats_score(
    session: Session, job: Job, profile: Profile, user_id: int | None = None
) -> float:
    """Rule-engine score of the Claude-tailored resume, or the base fallback."""
    llm = get_llm()
    if not llm.available:
        return _base_ats_score(profile, job)

    try:
        version = await tailor_for_job(session, job, profile, user_id=user_id)
    except TailorError as exc:
        log.debug("job %s: tailoring unavailable for ATS pass (%s)", job.id, exc)
        return _base_ats_score(profile, job)

    if version is None or not version.text.strip():
        return _base_ats_score(profile, job)
    # The tailored version already carries the rule-engine verdict on its own
    # rewritten text, so report that rather than re-running the rules.
    return version.ats_score_after


async def score_pending(session: Session, *, user_id: int, limit: int = 25) -> int:
    """Score up to `limit` of one user's unscored jobs. Returns how many were scored.

    Newest postings first: a fresh job is worth acting on before a stale one, and
    that ordering is what preserves the fast-apply advantage when a backlog builds.
    """
    profile = session.exec(
        select(Profile).where(Profile.user_id == user_id)
    ).first()
    if profile is None:
        log.info("user %s has no profile; skipping scoring", user_id)
        return 0
    if not (profile.base_resume_text or profile.skills):
        log.info("user %s profile has no resume text or skills; skipping", user_id)
        return 0

    # A job is a candidate when this user has no state row for it yet, or has
    # one still marked NEW. The outer join is what keeps users independent:
    # another user scoring a job no longer removes it from this user's queue.
    jobs = session.exec(
        select(Job)
        .outerjoin(
            UserJob,
            (UserJob.job_id == Job.id) & (UserJob.user_id == user_id),
        )
        .where(Job.expired_at.is_(None))
        .where(
            (UserJob.id.is_(None)) | (UserJob.status == JobStatus.NEW)
        )
        .order_by(Job.posted_at.desc().nullslast(), Job.first_seen_at.desc())
        .limit(limit)
    ).all()

    scored = 0
    for job in jobs:
        try:
            decision = await score_job(session, job, profile, user_id=user_id)
        except Exception:  # noqa: BLE001
            log.exception("scoring job %s failed", job.id)
            state = user_job(session, user_id, job.id)
            state.status = JobStatus.FAILED
            session.add(state)
            # Commit per item: with tier-2 awaiting LLM calls while holding this
            # session, an open SQLite write transaction would block every other
            # writer (login, polling) for the whole batch.
            session.commit()
            continue

        _persist_decision(session, job, decision, user_id=user_id)
        scored += 1
        # Same reasoning: release the write lock before the next job's LLM call.
        session.commit()

    return scored


def _persist_decision(
    session: Session, job: Job, decision: Decision, *, user_id: int
) -> None:
    existing = session.exec(
        select(JobScore).where(
            JobScore.job_id == job.id, JobScore.user_id == user_id
        )
    ).first()
    score = existing or JobScore(job_id=job.id, user_id=user_id)

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

    state = user_job(session, user_id, job.id)
    state.status = (
        JobStatus.REJECTED
        if decision.verdict in ("disqualified", "weak")
        else JobStatus.SCORED
    )

    session.add(score)
    session.add(state)


# ---------------------------------------------------------------- helpers

def _profile_match_text(profile: Profile) -> str:
    """Build the per-user evidence used by deterministic skill matching."""
    evidence = profile.github_evidence if isinstance(profile.github_evidence, dict) else {}
    github_skills = evidence.get("skills") or []
    parts = [profile.base_resume_text or ""]
    parts.append("Stated skills: " + ", ".join(profile.skills or []))
    parts.append("GitHub skills: " + ", ".join(str(s) for s in github_skills))
    return "\n".join(part for part in parts if part.strip())


def _profile_block(profile: Profile) -> str:
    parts = [
        f"Name: {profile.full_name or 'not given'}",
        f"Years of experience: {profile.years_experience if profile.years_experience is not None else 'not given'}",
        f"Work authorization: {profile.work_authorization or 'not given'}",
        f"Location: {profile.location or 'not given'}",
        f"Target roles: {', '.join(profile.target_titles) if profile.target_titles else 'not specified'}",
        f"Target locations: {', '.join(profile.target_locations) if profile.target_locations else 'not specified'}",
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
