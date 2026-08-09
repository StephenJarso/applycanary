"""DuckDuckGo web search connector.

Surfaces jobs that the curated boards and aggregators do not carry by searching
the web for the seeker's target roles. DuckDuckGo is used (not a search API)
because it needs no key and its lite HTML endpoint is scrapeable with `httpx`.

Two stages, like BrighterMonday:

1. Pull the SERP and collect result links. DuckDuckGo wraps every external URL in
   a `uddg=` redirect, so the real target URL is recovered from that parameter
   rather than trusted from the anchor href.
2. For each result, fetch the landing page and look for embedded JSON-LD
   `JobPosting` (the schema jobsites publish to Google). When a page has none,
   the result title + DDG snippet are synthesised into a RawJob so the user can
   still review it.

Fragile by design: markup and URL shapes change. `is_scraper = True` and
per-result error isolation (one dead link never fails a poll) keep the blast
radius small, and every run is recorded via `SourceRun` so a silent breakage is
visible on the dashboard.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from typing import Any
from urllib.parse import unquote, urlparse

from app.sources.base import (
    BaseSource,
    RawJob,
    clean_html,
    parse_epoch,
    register,
)
from app.sources.greenhouse import parse_iso

log = logging.getLogger(__name__)

SEARCH_URL = "https://lite.duckduckgo.com/lite/"
MAX_CONCURRENT_DETAIL = 5
DETAIL_TIMEOUT = 20.0

# An anchor wrapped around a `uddg=`-encoded target URL on the DDG lite SERP:
# <a ... href="https://duckduckgo.com/l/?uddg=<enc>...">title</a>
_RESULT_RE = re.compile(
    r'<a[^>]*href="[^"]*?uddg=([^&"]+)[^"]*"[^>]*>(.*?)</a>',
    re.IGNORECASE | re.DOTALL,
)
# Snippet text lives in the same result row, after the anchor.
_SNIPPET_RE = re.compile(
    r"uddg=([^&\"']+)[^<]*</a>(.*?)(?=<a[^>]*uddg=|\Z)",
    re.IGNORECASE | re.DOTALL,
)
_SCRIPT_RE = re.compile(
    r'<script[^>]*type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
    re.IGNORECASE | re.DOTALL,
)


@register
class WebSearchSource(BaseSource):
    name = "websearch"
    is_scraper = True

    def __init__(
        self,
        search_terms: list[str] | None = None,
        query: str | None = None,
        max_results: int = 25,
        **kw: Any,
    ) -> None:
        super().__init__(**kw)
        self.search_terms = [t.lower() for t in (search_terms or []) if t]
        self.query = query
        self.max_results = max_results

    async def fetch(self) -> list[RawJob]:
        results = _parse_results(await self._serp_html(), self.max_results)
        if not results:
            return []

        sem = asyncio.Semaphore(MAX_CONCURRENT_DETAIL)

        async def _one(url: str, title: str, snippet: str) -> RawJob | None:
            async with sem:
                try:
                    resp = await self.client.get(
                        url,
                        headers={"Accept": "text/html"},
                        timeout=DETAIL_TIMEOUT,
                    )
                    resp.raise_for_status()
                except Exception as exc:  # noqa: BLE001 - one dead link is not a poll failure
                    log.warning("websearch: detail %s failed: %s", url, exc)
                    return _synthesise(url, title, snippet)
                return _parse_jobposting(resp.text, url, title) or _synthesise(
                    url, title, snippet
                )

        raw = await asyncio.gather(*(_one(u, t, s) for u, t, s in results))
        jobs = [j for j in raw if j is not None]
        return [j for j in jobs if self._matches(j)]

    def _matches(self, job: RawJob) -> bool:
        if not self.search_terms:
            return True
        haystack = f"{job.title} {job.description} {job.extra.get('tags', '')}".lower()
        return any(term in haystack for term in self.search_terms)

    def _q(self) -> str:
        if self.query:
            return self.query
        if self.search_terms:
            return f"{' '.join(self.search_terms)} jobs"
        return "software engineer jobs"

    async def _serp_html(self) -> str:
        resp = await self.client.get(
            SEARCH_URL, params={"q": self._q(), "kl": "us-en"},
            headers={"Accept": "text/html"},
        )
        resp.raise_for_status()
        return resp.text


# ---------------------------------------------------------------- parsing (pure, testable)


def _parse_results(html: str, limit: int) -> list[tuple[str, str, str]]:
    """Return (url, title, snippet) triples from a DDG lite SERP."""
    seen: set[str] = set()
    out: list[tuple[str, str, str]] = []
    for m in _RESULT_RE.finditer(html):
        url = _normalise_target(unquote(m.group(1)))
        if not url or url in seen:
            continue
        title = _text(m.group(2))
        snippet_match = _SNIPPET_RE.search(html, m.start())
        snippet = _text(snippet_match.group(2) if snippet_match else None)
        seen.add(url)
        out.append((url, title, snippet))
        if len(out) >= limit:
            break
    return out


def _normalise_target(url: str) -> str:
    """Only follow http(s) targets; drop DDG's own /l/ redirect wrappers."""
    if not url:
        return ""
    try:
        parsed = urlparse(url)
    except (ValueError, TypeError):
        return ""
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        return ""
    return url


def _text(fragment: str | None) -> str:
    if not fragment:
        return ""
    return clean_html(re.sub(r"<[^>]+>", " ", fragment))


def _parse_jobposting(html: str, url: str, title_hint: str = "") -> RawJob | None:
    posting = _extract_jobposting_jsonld(html)
    if not isinstance(posting, dict):
        return None

    hiring = posting.get("hiringOrganization")
    company = _str(hiring.get("name")) if isinstance(hiring, dict) else _str(hiring)
    return RawJob(
        source="websearch",
        source_id=_str(posting.get("id") or url),
        company=company or _guess_company(url),
        title=str(posting.get("title") or title_hint).strip() or _guess_title(url),
        location=_location(posting.get("jobLocation")),
        description=clean_html(_str(posting.get("description"))),
        apply_url=url,
        posted_at=parse_iso(posting.get("datePosted")) or parse_epoch(posting.get("datePosted")),
        remote_flag=None,
        extra={"tags": str(posting.get("employmentType") or "")},
    )


def _extract_jobposting_jsonld(html: str) -> dict | None:
    """First `JobPosting` node found in the page's JSON-LD blocks."""
    for script in _SCRIPT_RE.findall(html):
        try:
            data = json.loads(script)
        except (ValueError, TypeError):
            continue
        if isinstance(data, dict):
            candidates = data.get("@graph") if isinstance(data.get("@graph"), list) else [data]
        else:
            candidates = []
        for node in candidates:
            if isinstance(node, dict) and node.get("@type") == "JobPosting":
                return node
    return None


def _location(node: object) -> str:
    if isinstance(node, str):
        return node
    if isinstance(node, dict):
        addr = node.get("address")
        if isinstance(addr, dict):
            return ", ".join(
                str(v) for v in (
                    addr.get("addressLocality"),
                    addr.get("addressRegion"),
                    addr.get("addressCountry"),
                )
                if v
            )
    return ""


def _guess_company(url: str) -> str:
    try:
        host = urlparse(url).netloc.lower().removeprefix("www.") or ""
    except (ValueError, TypeError):
        host = ""
    return host


def _guess_title(url: str) -> str:
    """Last path segment as a last-resort title."""
    try:
        path = urlparse(url).path.strip("/") or ""
    except (ValueError, TypeError):
        path = ""
    return path.rsplit("/", 1)[-1].replace("-", " ").title() or "Remote job"


def _str(value: object) -> str:
    return str(value).strip() if value is not None else ""


def _synthesise(url: str, title: str, snippet: str) -> RawJob:
    return RawJob(
        source="websearch",
        source_id=url,
        company=_guess_company(url),
        title=title or _guess_title(url),
        description=(snippet or "")[:2000],
        apply_url=url,
        posted_at=None,
        remote_flag=None,
    )
