"""Profile-driven relevance filters for tier-1 scoring.

The profile already records what the user wants (``target_titles``,
``years_experience``, ``work_authorization``); this module is what *acts* on
those fields so only postings that actually match the seeker reach the expensive
tier-2 pass — and only those are shown on the dashboard by default.

Relevance here is intentionally conservative. A filter that is too aggressive
hides a real opening (a silent failure). Each check only rejects a posting that
*cannot* be the role the user is after, and every rejection is recorded with a
human-readable reason so the user can audit it.
"""

from __future__ import annotations

import logging

from rapidfuzz import fuzz

from app.models import Job, Profile
from app.pipeline.normalize import norm_title

log = logging.getLogger(__name__)

# Seniority tokens mapped to a coarse rank. Higher == more senior / broader scope.
# Management tokens are ranked highest on purpose: an IC applying for IC roles
# should not be served Director / VP requisitions.
_SENIORITY_RANK: dict[str, int] = {
    "intern": 0, "internship": 0, "graduate": 0, "jr": 0, "junior": 0,
    "ii": 1, "iii": 1, "iv": 1, "level": 1, "l1": 1, "l2": 1,
    "mid": 2, "midlevel": 2, "mid-level": 2,
    "associate": 2, "assoc": 2,
    "senior": 3, "sr": 3, "lead": 3,
    "staff": 4, "principal": 4, "architect": 4,
    "manager": 5, "head": 5, "director": 5, "vp": 5, "chief": 5, "cto": 5,
}
# Baseline rank for a title with no seniority token ("Software Engineer").
_DEFAULT_RANK = 2
# Words that mark scope/role, not pure seniority, so "junior engineer" does not
# match "engineering manager" on the shared word "engineer".
SCOPE_WORDS = {
    "manager", "lead", "head", "director", "vp", "principal", "staff",
    "intern", "internship", "graduate", "junior", "senior", "sr", "jr",
    "architect", "consultant", "chief",
}


def _tokens(title: str) -> set[str]:
    return {t for t in norm_title(title).split() if t}


def _core_tokens(title: str) -> set[str]:
    """Role tokens of ``title`` minus seniority/scope markers.

    'Senior Frontend Engineer' -> {'frontend', 'engineer'}
    'Engineering Manager' -> {'engineering'}
    """
    tokens = [t for t in norm_title(title).split() if t and t not in SCOPE_WORDS]
    return {t for t in tokens if t not in {"i", "ii", "iii", "iv", "v"}}


def title_matches_target(title: str, targets: list[str]) -> bool:
    """Does ``title`` correspond to any of the seeker's ``targets``?

    Seniority/scope words are stripped before the fuzzy compare so that
    'Senior Frontend Engineer' matches a target of 'Frontend Engineer', but
    'Frontend Engineer' does NOT match a target of 'Backend Engineer'.
    Returns True when no targets are configured (filter is opt-in).
    """
    if not targets:
        return True
    job_core = _core_tokens(title)
    if not job_core:
        return False
    for target in targets:
        target_core = _core_tokens(target)
        if not target_core:
            continue
        ratio = fuzz.token_set_ratio(
            " ".join(sorted(job_core)), " ".join(sorted(target_core))
        )
        if ratio >= 85:
            return True
    return False


def title_rank(title: str) -> int:
    """Coarse seniority rank of a title (max seniority token, else baseline)."""
    ranks = [_SENIORITY_RANK[t] for t in _tokens(title) if t in _SENIORITY_RANK]
    return max(ranks) if ranks else _DEFAULT_RANK


def max_rank_for_experience(years: int | None) -> int:
    """Highest seniority rank the seeker is expected to consider.

    Bands are deliberately coarse and conservative: a seeker with a couple of
    years of experience is still served senior-IC roles (many accept 2-3y), while
    staff/principal and management are only reached once the experience to
    plausibly hold them is on file.
    """
    if years is None:
        return 5  # no data -> do not filter on seniority.
    if years <= 1:
        return _SENIORITY_RANK["junior"]  # entry/junior only
    if years <= 3:
        return _SENIORITY_RANK["senior"]  # senior IC
    if years <= 5:
        return _SENIORITY_RANK["staff"]  # senior staff / principal IC
    return _SENIORITY_RANK["manager"]  # enough experience to read mgmt roles


def seniority_matches(title: str, years_experience: int | None) -> bool:
    """Is the title's seniority within the seeker's band?"""
    if years_experience is None:
        return True
    return title_rank(title) <= max_rank_for_experience(years_experience)


def relevance_disqualifier(job: Job, profile: Profile) -> str:
    """Return a reason string if the job is not relevant to the seeker, else ""."""
    targets = profile.target_titles or []
    if targets and not title_matches_target(job.title, targets):
        return (
            f"title {job.title!r} is not one of your target roles "
            f"({', '.join(targets)})"
        )
    if not seniority_matches(job.title, profile.years_experience):
        allowed = max_rank_for_experience(profile.years_experience)
        return (
            f"role {job.title!r} (seniority rank {title_rank(job.title)}) exceeds "
            f"your experience band (max rank {allowed}, years="
            f"{profile.years_experience})"
        )
    return ""
