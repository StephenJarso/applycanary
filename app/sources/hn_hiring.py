"""Hacker News "Who is hiring?" connector.

The monthly thread is where small teams and unusual roles are posted directly
by the people doing the hiring — often days before (or instead of) a job board.
Each top-level comment is one posting, usually with an official link.

The thread is read through Algolia's public HN API, so there is no scraping of
the HN site and no API key. Comments are free text, so header parsing is
deliberately forgiving: `Company | Role | Location` and
`Company (Location) — Role` are both understood, and anything else falls back
to the comment's first line plus the linked domain as the company.
"""

from __future__ import annotations

import logging
import re
from html import unescape
from typing import Any
from urllib.parse import urlparse

from app.sources.base import BaseSource, RawJob, clean_html, parse_epoch, register

log = logging.getLogger(__name__)

ALGOLIA = "https://hn.algolia.com/api/v1"
MAX_DESCRIPTION_CHARS = 20000
DEFAULT_TERMS = ["engineer", "developer", "backend", "golang", "python", "data"]

_PARAGRAPH_RE = re.compile(r"<p\s*/?>", re.IGNORECASE)
_LINK_RE = re.compile(r'<a\b[^>]*href="([^"]+)"[^>]*>(.*?)</a>', re.IGNORECASE | re.DOTALL)
_LOCATION_HINTS = (
    "remote", "onsite", "on-site", "hybrid", "usa", "us only", "europe", "emea",
    "asia", "africa", "japan", "tokyo", "kenya", "nairobi", "london", "berlin",
    "canada", "australia", "india", "worldwide", "global", "germany", "france",
    "netherlands", "spain", "portugal", "poland", "uk", "united kingdom",
    "new york", "san francisco", "california", "ontario", "toronto", "singapore",
)
_ROLE_HINTS = (
    "engineer", "developer", "designer", "scientist", "manager", "architect",
    "analyst", "researcher", "sre", "devops", "data", "product", "intern",
    "lead", "frontend", "front-end", "backend", "back-end", "full-stack",
    "full stack", "platform", "cloud", "security", "qa", "test", "mobile",
    "ios", "android", "machine learning", "ml ", "ai ", "software", "swe",
)


@register
class HNHiringSource(BaseSource):
    name = "hn_hiring"
    is_scraper = False

    def __init__(
        self,
        search_terms: list[str] | None = None,
        max_results: int = 120,
        **kw: Any,
    ) -> None:
        super().__init__(**kw)
        self.search_terms = [t.lower() for t in (search_terms or DEFAULT_TERMS) if t]
        self.max_results = max(1, min(int(max_results), 300))

    async def fetch(self) -> list[RawJob]:
        story = await self._latest_story()
        if story is None:
            return []
        thread = await self.get_json(f"{ALGOLIA}/items/{story['objectID']}")
        children = thread.get("children") if isinstance(thread, dict) else None
        if not isinstance(children, list):
            return []

        jobs: list[RawJob] = []
        for comment in children:
            if len(jobs) >= self.max_results:
                break
            if not isinstance(comment, dict):
                continue
            job = self._parse(comment)
            if job is not None:
                jobs.append(job)
        log.info("hn_hiring: %d postings from %s", len(jobs), story.get("title"))
        return jobs

    async def _latest_story(self) -> dict | None:
        payload = await self.get_json(
            f"{ALGOLIA}/search_by_date",
            params={
                "tags": "story,author_whoishiring",
                "hitsPerPage": 12,
            },
        )
        hits = payload.get("hits") if isinstance(payload, dict) else None
        if not isinstance(hits, list):
            return None
        for hit in hits:
            if not isinstance(hit, dict):
                continue
            title = str(hit.get("title") or "")
            if title.startswith("Ask HN: Who is hiring?"):
                return hit
        return None

    def _parse(self, comment: dict) -> RawJob | None:
        text = str(comment.get("text") or "")
        plain = clean_html(text)
        if not plain:
            return None
        if self.search_terms and not _matches(plain, self.search_terms):
            return None

        header = clean_html(_PARAGRAPH_RE.split(text, maxsplit=1)[0])
        company, title, location = _split_header(header)
        apply_url = _best_link(text) or (
            f"https://news.ycombinator.com/item?id={comment.get('id')}"
        )
        if not company:
            company = _domain(apply_url)

        if not title:
            title = header[:120].strip()
        if not company or not title:
            return None

        return RawJob(
            source=self.name,
            source_id=str(comment.get("id") or apply_url),
            company=company,
            title=title,
            location=location,
            description=plain[:MAX_DESCRIPTION_CHARS],
            apply_url=apply_url,
            posted_at=parse_epoch(comment.get("created_at_i")),
            extra={
                "hn_url": f"https://news.ycombinator.com/item?id={comment.get('id')}",
                "official": True,
            },
        )


# ---------------------------------------------------------------- pure helpers


def _matches(text: str, terms: list[str]) -> bool:
    haystack = text.lower()
    for term in terms:
        if term == "go":
            if re.search(r"\bgo\b", haystack):
                return True
        elif term in haystack:
            return True
    return False


def _split_header(header: str) -> tuple[str, str, str]:
    """Best-effort (company, title, location) from an HN comment's first line."""
    header = " ".join(header.split())
    if not header:
        return "", "", ""

    first = re.split(r"(?<=[.!?])\s+", header, maxsplit=1)[0][:240]

    if "|" in first:
        parts = [p.strip(" -–—") for p in first.split("|") if p.strip(" -–—")]
        if len(parts) >= 2:
            company, captured = _strip_location(_strip_urls(parts[0]))
            rest = parts[1:]
            location = captured or next((p for p in rest if _looks_location(p)), "")
            title = next(
                (p for p in rest if p != location and _looks_role(p)),
                "",
            )
            if not title and location:
                # "Company | ONSITE | City" with the role only in the body.
                title = "Multiple roles"
            return company, title, location

    for dash in (" — ", " – ", " - "):
        if dash in first:
            company, _, rest = first.partition(dash)
            role = rest.split(dash, 1)[0]
            company, captured = _strip_location(company)
            return company, role.strip(), captured

    company, captured = _strip_location(first)
    if captured:
        return company, "", captured
    # No separator and no location: the line is a sentence, not a company name.
    # Let the caller fall back to the linked domain.
    return "", first, ""


def _strip_location(value: str) -> tuple[str, str]:
    """Split 'Acme (Remote US)' into ('Acme', 'Remote US')."""
    match = re.match(r"^(.*?)\s*\(([^)]{2,80})\)\s*$", value)
    if match and _looks_location(match.group(2)):
        return match.group(1).strip(), match.group(2).strip()
    return value.strip(), ""


def _looks_location(value: str) -> bool:
    low = value.lower()
    return any(hint in low for hint in _LOCATION_HINTS)


def _looks_role(value: str) -> bool:
    low = value.lower()
    if any(x in low for x in ("full-time", "part-time", "contract", "salary", "equity")):
        return False
    return any(hint in low for hint in _ROLE_HINTS)


def _strip_urls(value: str) -> str:
    return re.sub(r"https?://\S+", "", value).strip(" -–—/")


def _best_link(text: str) -> str:
    best = ("", -1)
    for href, label in _LINK_RE.findall(text):
        href = unescape(href).strip()
        if not href.startswith(("http://", "https://")):
            continue
        host = urlparse(href).netloc.lower()
        if host.endswith("news.ycombinator.com"):
            continue
        score = 0
        low = f"{href} {clean_html(label)}".lower()
        if any(k in low for k in ("career", "jobs", "apply", "hiring", "positions")):
            score += 3
        if not host.startswith("www."):
            score += 1
        if score > best[1]:
            best = (href, score)
    return best[0]


def _domain(url: str) -> str:
    host = urlparse(url).netloc.lower().removeprefix("www.")
    return host.split(":")[0]
