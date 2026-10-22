"""Role-driven job discovery: go find the user's actual target roles.

The broad poll pulls whole boards and filters locally; this job instead goes
out and *looks* for the user's roles. For every active profile it builds search
queries from `target_titles` + `skills` + GitHub-evidence skills, then runs the
Adzuna API (structured, global) and the web-search connector (free, DuckDuckGo
lite) with those queries. New postings land in the shared pool through the
normal dedup path and are scored for that user immediately, so they show up in
the dashboard without waiting for the next scoring cycle.

Adzuna's free tier is 500 requests/month, so the cadence is deliberately slow
(6 hours) and calls are budgeted: once the rolling 30-day count passes
`ADZUNA_MONTHLY_BUDGET`, Adzuna is skipped for the rest of the window. Web
search is free and unbudgeted.
"""

from __future__ import annotations

import logging

import httpx
from sqlmodel import select

from app.config import get_settings
from app.db import session_scope
from app.models import Profile, SourceRun, User, utcnow
from app.pipeline.ingest import IngestSummary, run_sources
from app.sources import adzuna, websearch

log = logging.getLogger(__name__)

# At most this many distinct queries per profile per cycle. Web search is free;
# only the first MAX_ADZUNA_QUERIES go to Adzuna so its monthly budget lasts.
MAX_QUERIES_PER_PROFILE = 6
MAX_ADZUNA_QUERIES = 3
# Rolling 30-day ceiling on Adzuna calls (free tier = 500/month; stay clear so
# the regular broad poll's own Adzuna usage never tips the account over).
ADZUNA_MONTHLY_BUDGET = 450
# Source labels recorded in SourceRun so discovery telemetry is separate from
# the broad poll's runs of the same connectors.
DISCOVER_ADZUNA = "discover:adzuna"
DISCOVER_WEB = "discover:websearch"

DEFAULT_TIMEOUT = 20.0


# A skill is not a search phrase. "go docker python kubernetes job posting" is
# too narrow for a search engine; "golang developer" is what a hiring page
# actually says. Map the skills we can turn into role phrases and fall back to
# the combined phrase only when none apply.
_SKILL_ROLE_QUERIES: dict[str, str] = {
    "go": "golang developer",
    "golang": "golang developer",
    "python": "python developer",
    "typescript": "typescript developer",
    "javascript": "javascript developer",
    "rust": "rust developer",
    "java": "java developer",
    "kotlin": "kotlin developer",
    "c#": "c# developer",
    "c++": "c++ developer",
    "react": "react developer",
    "backend": "backend engineer",
    "frontend": "frontend engineer",
    "full stack": "full stack engineer",
    "fullstack": "full stack engineer",
    "kubernetes": "kubernetes engineer",
    "grpc": "grpc engineer",
    "aws": "aws engineer",
    "devops": "devops engineer",
    "sre": "site reliability engineer",
    "platform": "platform engineer",
}


def _profile_skills(profile) -> list[str]:  # noqa: ANN001
    """Stated skills first, then GitHub-evidence skills, lowercased and unique."""
    skills: list[str] = []
    evidence = profile.github_evidence if isinstance(profile.github_evidence, dict) else {}
    for source in (profile.skills or [], evidence.get("skills") or []):
        for value in source:
            skill = str(value).strip().lower()
            if skill and skill not in skills:
                skills.append(skill)
    return skills


def build_queries(profile) -> list[str]:  # noqa: ANN001
    """Up to MAX_QUERIES_PER_PROFILE search phrases for one profile.

    Target titles are the highest-signal phrases and come first. Then each
    skill that maps to a real role phrase (Go -> "golang developer", Kubernetes
    -> "kubernetes engineer") gets its own query, but only if the target titles
    do not already cover it. The old combined phrase remains as a last resort
    for profiles whose skills have no mapping.
    """
    queries: list[str] = []

    def add(query: str, *, related: bool = False) -> bool:
        query = " ".join(str(query).split())
        if not query:
            return False
        low = query.lower()
        if any(existing.lower() == low for existing in queries):
            return False
        # Do not add "golang developer" when "Golang Developer" is a target.
        # Internship/new-grad variants intentionally contain the target phrase.
        if not related and any(
            low in existing.lower() or existing.lower() in low for existing in queries
        ):
            return False
        queries.append(query)
        return True

    for title in (profile.target_titles or [])[:4]:
        t_str = str(title).strip()
        add(t_str)

    if len(queries) >= MAX_QUERIES_PER_PROFILE:
        return queries[:MAX_QUERIES_PER_PROFILE]

    skills = _profile_skills(profile)

    # Early-career and rare-opportunity variants are derived from the user's
    # primary target, so a Go profile also sees Go internships/new-grad roles
    # instead of only senior product engineering postings.
    primary = next((str(t).strip() for t in (profile.target_titles or []) if str(t).strip()), "")
    if not primary and skills:
        primary = _SKILL_ROLE_QUERIES.get(skills[0], "")
    if primary:
        lower = primary.lower()
        for label in ("internship", "new grad", "fellowship"):
            if label in lower:
                continue
            add(f"{primary} {label}", related=True)
            if len(queries) >= MAX_QUERIES_PER_PROFILE:
                return queries[:MAX_QUERIES_PER_PROFILE]

    # When the seeker is open to relocation, search for visa-sponsored postings
    # in addition to local/remote ones.
    if getattr(profile, "open_to_relocation", False) and primary:
        for suffix in ("visa sponsorship", "relocation package"):
            add(f"{primary} {suffix}", related=True)
            if len(queries) >= MAX_QUERIES_PER_PROFILE:
                return queries[:MAX_QUERIES_PER_PROFILE]

    role_added = False
    for skill in skills:
        role = _SKILL_ROLE_QUERIES.get(skill)
        if role:
            role_added = add(role) or role_added
            if len(queries) >= MAX_QUERIES_PER_PROFILE:
                return queries[:MAX_QUERIES_PER_PROFILE]

    # Only use the old keyword-soup query when no skill mapped to a real role.
    if len(queries) < MAX_QUERIES_PER_PROFILE and skills and not role_added:
        add(" ".join(skills[:4]))

    return queries[:MAX_QUERIES_PER_PROFILE]


def _adzuna_budget_left(session) -> int:  # noqa: ANN001
    from datetime import timedelta

    from sqlmodel import func

    since = utcnow() - timedelta(days=30)
    used = session.exec(
        select(func.count(SourceRun.id)).where(
            SourceRun.source.in_([DISCOVER_ADZUNA, "adzuna"]),
            SourceRun.started_at >= since,
        )
    ).one()
    return ADZUNA_MONTHLY_BUDGET - int(used)


def _active_profiles(session) -> list[Profile]:  # noqa: ANN001
    return list(
        session.exec(
            select(Profile).join(User, User.id == Profile.user_id).where(User.is_active)
        ).all()
    )


async def _fetch_queries(
    queries: list[str],
    *,
    allow_adzuna: bool,
    client: httpx.AsyncClient,
) -> IngestSummary:
    """Run one profile's queries through the connectors; persist via run_sources.

    Source labels are overridden per instance so discovery shows up on the
    sources dashboard under its own name rather than blurring into the broad
    poll's adzuna/websearch rows.
    """
    sources = []
    for index, q in enumerate(queries):
        if allow_adzuna and index < MAX_ADZUNA_QUERIES:
            sources.append(adzuna.AdzunaSource(
                search_terms=[q], country="us", location="", max_results=25,
                client=client,
            ))
        sources.append(websearch.WebSearchSource(
            query=f"{q} job posting", max_results=15, client=client,
        ))

    if queries:
        primary_q = queries[0]
        try:
            from app.sources import jobicy as jobicy_mod
            from app.sources import linkedin as linkedin_mod
            from app.sources import remotive as remotive_mod

            sources.append(remotive_mod.RemotiveSource(
                search_terms=[primary_q], limit=40, client=client,
            ))
            sources.append(jobicy_mod.JobicySource(
                search_terms=[primary_q], count=30, client=client,
            ))
            sources.append(linkedin_mod.LinkedInSource(
                search_terms=[primary_q], max_results=20, max_queries=2, client=client,
            ))
        except Exception as exc:  # noqa: BLE001
            log.warning("failed to attach supplementary sources for discovery: %s", exc)

    for src in sources:
        if src.name not in ("remotive", "jobicy", "linkedin"):
            src.name = DISCOVER_ADZUNA if src.name == "adzuna" else DISCOVER_WEB
        src._client = client  # noqa: SLF001 - share one pooled client
        src._owns_client = False  # noqa: SLF001
    return await run_sources(sources)


async def discover_for_user(user_id: int) -> dict:
    """Single-user discovery, for the \"Discover for me\" action in the UI."""
    with session_scope() as session:
        profile = session.exec(
            select(Profile).where(Profile.user_id == user_id)
        ).first()
        if profile is None:
            return {"ok": False, "message": "no profile configured; save one first"}
        queries = build_queries(profile)
        if not queries:
            return {
                "ok": False,
                "message": "add target titles or skills to your profile first",
            }
        allow_adzuna = _adzuna_budget_left(session) > 0

    summary = IngestSummary()
    async with httpx.AsyncClient(timeout=DEFAULT_TIMEOUT) as client:
        summary = await _fetch_queries(queries, allow_adzuna=allow_adzuna, client=client)

    scored = 0
    with session_scope() as session:
        from app.pipeline.score import score_pending

        scored = await score_pending(session, user_id=user_id, limit=50)

    return {
        "ok": True,
        "message": (
            f"Queried {len(queries)} role phrases, found {summary.found} "
            f"({summary.new} new); scored {scored} for you."
        ),
        "detail": {
            "queries": queries,
            "found": summary.found,
            "new": summary.new,
            "scored": scored,
            "adzuna": allow_adzuna,
            "errors": summary.errors,
        },
    }


async def run_discovery() -> IngestSummary:
    """One discovery cycle across every active profile."""
    settings = get_settings()
    adzuna_configured = bool(settings.adzuna_app_id and settings.adzuna_app_key)

    total = IngestSummary()
    with session_scope() as session:
        profiles = [
            (profile, build_queries(profile))
            for profile in _active_profiles(session)
        ]
        budget_left = _adzuna_budget_left(session)
    profiles = [(p, q) for p, q in profiles if q]
    if not profiles:
        log.info("discover: no active profiles with role/skill terms")
        return total

    async with httpx.AsyncClient(timeout=DEFAULT_TIMEOUT) as client:
        for profile, queries in profiles:
            allow_adzuna = adzuna_configured and budget_left > 0
            summary = await _fetch_queries(
                queries, allow_adzuna=allow_adzuna, client=client
            )
            if allow_adzuna:
                budget_left = max(0, budget_left - len(queries))
            _merge(total, summary)
            if summary.new:
                log.info(
                    "discover: %d new job(s) for user %s via %r",
                    summary.new, profile.user_id, queries,
                )
            with session_scope() as session:
                from app.pipeline.score import score_pending

                scored = await score_pending(session, user_id=profile.user_id, limit=25)
                if scored:
                    log.info("discover: scored %d job(s) for user %s", scored, profile.user_id)

    log.info(
        "discover: %d profiles, %d sources, %d new, %d dup, %d failed",
        len(profiles), total.sources_run, total.new,
        total.duplicates, total.sources_failed,
    )
    return total


def _merge(total: IngestSummary, part: IngestSummary) -> None:
    total.sources_run += part.sources_run
    total.sources_failed += part.sources_failed
    total.found += part.found
    total.new += part.new
    total.duplicates += part.duplicates
    total.errors.extend(part.errors)
