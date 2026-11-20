"""Rare opportunity discovery: volunteerships, fellowships, OSS roles.

Runs periodically (or on-demand). Fetches from curated RSS feeds and
targeted DuckDuckGo searches, then returns scored and deduplicated leads
that match the user's skills and interests.

No LLM scoring pass — the volume is low enough that the user reviews every result.
Simple keyword relevance matching against profile skills and target titles is
enough to filter noise and rank matches.
"""
from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from urllib.parse import unquote

import httpx
from sqlmodel import select

from app.db import session_scope
from app.models import Profile, User
from app.sources.rss import fetch_rss

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------- feeds

OPPORTUNITY_FEEDS: list[tuple[str, str]] = [
    ("Opportunities for Africans", "https://opportunitiesforafricans.com/feed/"),
    ("Opportunities Circle", "https://opportunitiescircle.com/feed/"),
    ("She Code Africa", "https://jobs.shecodeafrica.org/feed/"),
    ("Africa Tech Jobs", "https://africatechjobs.com/feed/"),
]

# DuckDuckGo searches for rare opportunities (no API key needed)
DDG_LITE = "https://lite.duckduckgo.com/lite/"
_OPPORTUNITY_QUERIES = [
    "software engineer fellowship Africa {year}",
    "volunteer software developer Kenya",
    "open source fellowship developer {year}",
    "tech volunteership remote Africa {year}",
    "developer internship fellowship East Africa",
]

MAX_FEED_ENTRIES = 15
REQUEST_TIMEOUT = 20.0
USER_AGENT = "applycanary/0.1 (personal opportunity tracker)"


@dataclass
class OpportunityLead:
    """A discovered opportunity not yet in the curated list."""
    title: str
    org: str
    url: str
    description: str
    source: str
    published: datetime | None
    tags: list[str]
    relevance_score: float  # 0-1, keyword match against profile


# ---------------------------------------------------------------------- keyword matching

def _relevance(lead_text: str, profile: Profile) -> float:
    """Simple keyword overlap between opportunity text and profile. Returns 0-1."""
    skills: list[str] = []
    if profile.skills:
        skills.extend(str(s).lower() for s in profile.skills if s)
    titles: list[str] = []
    if profile.target_titles:
        titles.extend(str(t).lower() for t in profile.target_titles if t)

    if not skills and not titles:
        return 0.5  # no profile constraints → show everything

    haystack = lead_text.lower()
    keywords = {*skills, *titles}
    hits = sum(1 for kw in keywords if kw in haystack)
    return min(hits / max(len(keywords), 1), 1.0)


# ---------------------------------------------------------------------- discovery

async def _fetch_feeds() -> list[OpportunityLead]:
    """Pull all configured RSS feeds concurrently."""
    results: list[OpportunityLead] = []
    tasks = [fetch_rss(url, max_entries=MAX_FEED_ENTRIES) for _, url in OPPORTUNITY_FEEDS]
    names = [name for name, _ in OPPORTUNITY_FEEDS]
    fetched = await asyncio.gather(*tasks, return_exceptions=True)
    for source_name, entries in zip(names, fetched, strict=False):
        if isinstance(entries, Exception):
            log.warning("discover_opp: feed %r failed: %s", source_name, entries)
            continue
        for e in entries:
            results.append(OpportunityLead(
                title=e.title,
                org=e.org or source_name,
                url=e.url,
                description=e.description,
                source=source_name,
                published=e.published,
                tags=e.tags,
                relevance_score=0.0,
            ))
    return results


async def _ddg_search(query: str) -> list[OpportunityLead]:
    """Run one DuckDuckGo lite search and return raw leads."""
    _RESULT_RE = re.compile(
        r'<a[^>]*href="[^"]*?uddg=([^&"]+)[^"]*"[^>]*>(.*?)</a>',
        re.IGNORECASE | re.DOTALL,
    )
    _TAG_RE = re.compile(r"<[^>]+>")

    try:
        async with httpx.AsyncClient(
            headers={"User-Agent": USER_AGENT, "Accept": "text/html"},
            timeout=REQUEST_TIMEOUT,
            follow_redirects=True,
        ) as client:
            resp = await client.get(DDG_LITE, params={"q": query, "kl": "us-en"})
            resp.raise_for_status()
            html = resp.text
    except Exception as exc:  # noqa: BLE001
        log.warning("discover_opp: DDG search %r failed: %s", query, exc)
        return []

    leads: list[OpportunityLead] = []
    for m in _RESULT_RE.finditer(html):
        url = unquote(m.group(1))
        title = _TAG_RE.sub("", m.group(2)).strip()
        if title and url and url.startswith("http"):
            leads.append(OpportunityLead(
                title=title, org="", url=url,
                description="", source="websearch",
                published=None, tags=[], relevance_score=0.0,
            ))
    return leads[:5]


async def discover_opportunities_for_profile(profile: Profile) -> list[OpportunityLead]:
    """Full discovery run for one profile. Returns scored, sorted leads."""
    year = datetime.now(UTC).year

    feed_leads = await _fetch_feeds()

    query_tasks = [
        _ddg_search(q.format(year=year))
        for q in _OPPORTUNITY_QUERIES[:3]  # keep it polite
    ]
    search_batches = await asyncio.gather(*query_tasks, return_exceptions=True)
    search_leads: list[OpportunityLead] = []
    for batch in search_batches:
        if isinstance(batch, list):
            search_leads.extend(batch)

    all_leads = feed_leads + search_leads

    # Score and deduplicate
    seen_urls: set[str] = set()
    scored: list[OpportunityLead] = []
    for lead in all_leads:
        if lead.url in seen_urls:
            continue
        seen_urls.add(lead.url)
        text = f"{lead.title} {lead.description} {' '.join(lead.tags)}"
        lead.relevance_score = _relevance(text, profile)
        scored.append(lead)

    scored.sort(key=lambda item: item.relevance_score, reverse=True)
    log.info(
        "discover_opp: %d leads for user %s (feeds=%d, search=%d)",
        len(scored), profile.user_id, len(feed_leads), len(search_leads),
    )
    return scored


async def run_opportunity_discovery() -> None:
    """Scheduled entry point: discover for all active users and log results."""
    with session_scope() as session:
        profiles = list(
            session.exec(
                select(Profile).join(User, User.id == Profile.user_id).where(User.is_active)
            ).all()
        )
    for profile in profiles:
        try:
            leads = await discover_opportunities_for_profile(profile)
            log.info(
                "discover_opp: user %s → %d leads (top relevance %.2f)",
                profile.user_id,
                len(leads),
                leads[0].relevance_score if leads else 0.0,
            )
        except Exception:  # noqa: BLE001
            log.exception("discover_opp: failed for user %s", profile.user_id)
