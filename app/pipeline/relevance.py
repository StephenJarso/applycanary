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
import re

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
# Deduction applied when a posting's title falls outside ``target_titles``.
# Sized so a mismatched title alone cannot drop an otherwise-strong match below
# the tier-1 cutoff — it reorders results rather than hiding them.
TITLE_MISMATCH_PENALTY = 12.0
# Words that mark scope/role, not pure seniority, so "junior engineer" does not
# match "engineering manager" on the shared word "engineer".
SCOPE_WORDS = {
    "manager", "lead", "head", "director", "vp", "principal", "staff",
    "intern", "internship", "graduate", "junior", "senior", "sr", "jr",
    "architect", "consultant", "chief",
}

# Titles that describe another profession. Tier-1 keyword overlap cannot tell
# "Global Controller" from "Backend Engineer" — both can share vocabulary with a
# resume, and when a JD lists no recognisable skills the fallback term overlap
# scored unrelated roles at 100. These markers only disqualify titles that are
# clearly not software work; anything with an engineering signal stays
# scoreable, so an oddly titled Go role is still surfaced.
NON_ENGINEERING_TITLE_WORDS = {
    "sales", "marketing", "account", "executive", "recruiter", "recruiting",
    "talent", "controller", "finance", "financial", "accountant", "legal",
    "counsel", "support", "success", "program", "project", "coordinator",
    "specialist", "analyst", "consultant", "writer", "editor", "social",
    "media", "content", "brand", "growth", "partnerships", "customer",
    "operations", "admin", "administrator", "entry", "clerk", "assistant",
    "relations",
}
# "Engineer" alone is not enough (Sales Engineer, Customer Engineer), but these
# compounds are genuine software titles and must not trip the marker above.
TECH_COMPOUND_TITLES = (
    "product engineer", "product security", "platform engineer",
    "solutions engineer", "solution engineer", "salesforce developer",
    "technical staff", "member of technical staff",
)
ENGINEERING_TITLE_WORDS = {
    "engineer", "engineering", "developer", "programmer", "sde", "swe",
    "architect", "devops", "sre", "software", "backend", "frontend",
    "fullstack", "full", "stack", "platform", "infrastructure", "systems",
    "cloud", "data", "database", "security", "qa", "test", "automation",
    "mobile", "android", "ios", "embedded", "firmware", "machine",
    "learning", "ml", "ai", "api", "microservices", "blockchain", "game",
    "graphics", "distributed",
}

# Remote roles are only useful abroad when the employer will hire where the
# seeker lives. These appear in the posting's location or description.
GLOBAL_REMOTE_SIGNALS = (
    "worldwide", "work from anywhere", "anywhere in the world", "global remote",
    "globally remote", "location independent", "remote-first", "fully remote",
    "emea", "africa", "international candidates",
)
FOREIGN_REGION_PATTERN = re.compile(
    r"\b(?:united states|usa|u\.s\.?|us|canada|india|europe|united kingdom|uk|"
    r"germany|poland|brazil|australia|singapore|japan|china|mexico|ireland|"
    r"israel|philippines|pakistan|nigeria)\b"
)
# An onsite role abroad is acceptable when the employer will move or sponsor the
# candidate — the relocation case the seeker explicitly asks for.
VISA_OR_RELOCATION_SIGNALS = (
    "visa sponsorship", "visa support", "sponsorship available", "we sponsor",
    "relocation package", "relocation assistance", "relocation support",
    "relocation to", "work permit", "right to work", "relocate",
)
_NON_LOCATION_WORDS = {
    "remote", "hybrid", "onsite", "site", "any", "anywhere", "worldwide",
    "the", "and", "for",
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


def non_engineering_role_reason(title: str, targets: list[str]) -> str:
    """Reason to reject a title from a clearly different profession, else "".

    Only fatal when the seeker has target titles configured and the title is
    neither a target match nor a software title; a target list written as
    keywords ("golang", "backend") keeps its meaning because those titles still
    carry an engineering signal.
    """
    if not targets:
        return ""
    if title_matches_target(title, targets):
        return ""
    low = norm_title(title)
    if any(compound in low for compound in TECH_COMPOUND_TITLES):
        return ""
    tokens = _tokens(title)
    if tokens & NON_ENGINEERING_TITLE_WORDS:
        return f"role {title!r} is not a software engineering position"
    if not (tokens & ENGINEERING_TITLE_WORDS):
        return f"role {title!r} has no software engineering signal"
    return ""


def _home_words(profile: Profile) -> set[str]:
    """Country/city words the seeker can work from without relocation."""
    words: set[str] = set()
    for value in (profile.location, *profile.target_locations):
        for word in re.findall(r"[a-z]+", (value or "").lower()):
            if len(word) < 3 or word in _NON_LOCATION_WORDS:
                continue
            words.add(word)
    return words


def location_eligibility_reason(job: Job, profile: Profile) -> str:
    """Reject postings the seeker cannot legally take without a relocation.

    Two cases matter:

    * **Remote, but restricted abroad.** ``is_remote`` postings that name a
      specific foreign region (e.g. "Remote, US") are rejected unless the job
      description signals worldwide/EMEA/Africa eligibility or the seeker's
      home location matches the region.
    * **Onsite abroad without sponsorship.** The seeker will relocate only when
      the employer sponsors the visa or pays for the move, so such a posting is
      only allowed when the description says so.

    Both checks are bypassed when ``profile.open_to_relocation`` is True —
    the seeker has explicitly opted in to relocated/visa-sponsored roles.

    An unknown location is never fatal: absence of data is not evidence.
    """
    location = (job.location or "").strip()
    loc_low = location.lower()
    desc_low = (job.description or "").lower()
    home = _home_words(profile)

    # Seeker explicitly opted in to relocation/visa-sponsored roles: let
    # everything through.  Individual job descriptions are still evaluated by
    # the LLM in tier-2, which will note the relocation requirement.
    if getattr(profile, "open_to_relocation", False):
        return ""

    if job.is_remote:
        if not loc_low or not home:
            return ""
        if any(word in loc_low for word in home):
            return ""
        if any(signal in desc_low for signal in GLOBAL_REMOTE_SIGNALS):
            return ""
        if any(signal in loc_low for signal in GLOBAL_REMOTE_SIGNALS):
            return ""
        if FOREIGN_REGION_PATTERN.search(loc_low):
            return (
                f"remote role is limited to {location!r}; you are based in "
                f"{profile.location or 'another region'}"
            )
        return ""

    targets = [loc.strip().lower() for loc in profile.target_locations if loc.strip()]
    if not targets or not location:
        return ""
    if any(target in loc_low for target in targets):
        return ""
    if any(signal in desc_low for signal in VISA_OR_RELOCATION_SIGNALS):
        return ""
    return (
        f"location {location!r} is outside your target locations and the "
        "posting offers no visa sponsorship or relocation support"
    )


def relevance_disqualifier(job: Job, profile: Profile) -> str:
    """Return a reason string if the job is categorically wrong for the seeker.

    Only seniority is fatal here. A title that does not match ``target_titles``
    is *not* disqualifying: those entries are frequently written as search
    keywords ("golang", "backend") rather than real titles, and treating a
    mismatch as fatal silently rejected every posting before the LLM ever saw
    it. Title distance is reported by `title_mismatch_penalty` and applied as a
    score deduction instead, which keeps the signal without the false negatives.
    """
    if not seniority_matches(job.title, profile.years_experience):
        allowed = max_rank_for_experience(profile.years_experience)
        return (
            f"role {job.title!r} (seniority rank {title_rank(job.title)}) exceeds "
            f"your experience band (max rank {allowed}, years="
            f"{profile.years_experience})"
        )
    return ""


def title_mismatch_penalty(job: Job, profile: Profile) -> tuple[float, str]:
    """Score deduction for a title outside ``target_titles``, plus its reason.

    Returns ``(0.0, "")`` when the profile sets no targets or the title matches.
    The deduction is deliberately moderate: a mismatch is evidence the posting
    is off-target, not proof, and the LLM pass that follows sees the full
    description and can override a bad title match either way.
    """
    targets = [t for t in (profile.target_titles or []) if t.strip()]
    if not targets or title_matches_target(job.title, targets):
        return 0.0, ""

    # If the user specified a language/keyword target like "Golang" or "Go",
    # check if the job is a general backend/software role and mentions Go in the description.
    t_lows = [t.lower() for t in targets]
    has_go_target = any(
        t in ("go", "golang") or "golang" in t or "go developer" in t for t in t_lows
    )
    if has_go_target:
        job_low = job.title.lower()
        if any(w in job_low for w in ("software", "backend", "developer", "engineer", "systems", "platform")):
            desc_head = (job.description or "")[:1000].lower()
            if "golang" in desc_head or " go " in desc_head or "\ngo " in desc_head or "/go" in desc_head:
                return 0.0, ""

    return TITLE_MISMATCH_PENALTY, (
        f"title {job.title!r} is outside your target roles "
        f"({', '.join(targets)})"
    )
