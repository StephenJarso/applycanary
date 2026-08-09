"""MyJobMag Kenya — HTML scraper.

    https://www.myjobmag.co.ke/jobs

Extracts job links from the listing page, then fetches each detail page to
build a complete ``RawJob``. Titles on the listing page follow the pattern
``Job Title at Company Name``, which is parsed to separate the two.

This is a pure HTML scraper with no structured data to lean on, making it
the most fragile of the Kenyan connectors. **Will break on markup changes.**
"""

from __future__ import annotations

import asyncio
import logging
import re
from typing import Any

from app.sources.base import BaseSource, RawJob, clean_html, register

log = logging.getLogger(__name__)

BASE_URL = "https://www.myjobmag.co.ke"
LISTING_URL = f"{BASE_URL}/jobs"
MAX_PAGES = 2
_JOB_LINK_RE = re.compile(
    r'<a[^>]*href="(/job/[^"]+)"[^>]*>(.*?)</a>', re.DOTALL
)
_HTML_TAG_RE = re.compile(r"<[^>]+>")


@register
class MyJobMagSource(BaseSource):
    name = "myjobmag"
    is_scraper = True

    def __init__(
        self,
        search_terms: list[str] | None = None,
        **kw: Any,
    ) -> None:
        super().__init__(**kw)
        self.search_terms = [t.lower() for t in (search_terms or []) if t]

    async def fetch(self) -> list[RawJob]:
        # Collect (url, title_text) pairs from listing pages
        listings: dict[str, str] = {}
        for page in range(1, MAX_PAGES + 1):
            try:
                resp = await self.client.get(
                    LISTING_URL,
                    params={"page": page},
                    headers={"Accept": "text/html"},
                )
                resp.raise_for_status()
                for path, raw_text in _JOB_LINK_RE.findall(resp.text):
                    title = _HTML_TAG_RE.sub(" ", raw_text).strip()
                    title = re.sub(r"\s+", " ", title)
                    if title and path not in listings:
                        listings[path] = title
            except Exception as exc:  # noqa: BLE001
                log.warning("myjobmag: listing page %d failed: %s", page, exc)

        if not listings:
            return []

        sem = asyncio.Semaphore(3)
        jobs: list[RawJob] = []

        async def _fetch_detail(path: str, title: str) -> RawJob | None:
            url = f"{BASE_URL}{path}"
            async with sem:
                try:
                    resp = await self.client.get(
                        url, headers={"Accept": "text/html"}
                    )
                    resp.raise_for_status()
                    return self._parse(path, title, url, resp.text)
                except Exception as exc:  # noqa: BLE001
                    log.warning("myjobmag: detail %s failed: %s", url, exc)
                    # Fallback: create a job from listing-page info only
                    return self._from_listing(path, title, url)

        results = await asyncio.gather(
            *(_fetch_detail(p, t) for p, t in listings.items())
        )
        for job in results:
            if job is not None and self._matches(job):
                jobs.append(job)
        return jobs

    def _matches(self, job: RawJob) -> bool:
        if not self.search_terms:
            return True
        haystack = job.title.lower()
        return any(term in haystack for term in self.search_terms)

    @staticmethod
    def _split_title(raw_title: str) -> tuple[str, str]:
        """Split 'Job Title at Company Name' into (title, company)."""
        # Common patterns: " at ", " - "
        for sep in (" at ", " At ", " – ", " - "):
            if sep in raw_title:
                parts = raw_title.rsplit(sep, 1)
                return parts[0].strip(), parts[1].strip()
        return raw_title.strip(), ""

    def _parse(
        self, path: str, listing_title: str, url: str, html: str
    ) -> RawJob:
        title, company = self._split_title(listing_title)
        slug = path.rsplit("/", 1)[-1]

        # Try to extract description from the detail page
        desc_match = re.search(
            r'<div[^>]*class="[^"]*job-?desc[^"]*"[^>]*>(.*?)</div>',
            html,
            re.DOTALL | re.IGNORECASE,
        )
        description = ""
        if desc_match:
            description = clean_html(desc_match.group(1))
        else:
            # Broader fallback: grab the article / main content
            article = re.search(
                r"<article[^>]*>(.*?)</article>", html, re.DOTALL
            )
            if article:
                description = clean_html(article.group(1))

        # Try to extract location
        location = "Kenya"
        loc_match = re.search(
            r'<span[^>]*class="[^"]*location[^"]*"[^>]*>(.*?)</span>',
            html,
            re.DOTALL | re.IGNORECASE,
        )
        if loc_match:
            location = _HTML_TAG_RE.sub("", loc_match.group(1)).strip() or "Kenya"

        return RawJob(
            source=self.name,
            source_id=slug,
            company=company,
            title=title,
            location=location,
            description=description,
            apply_url=url,
        )

    def _from_listing(self, path: str, listing_title: str, url: str) -> RawJob:
        """Minimal RawJob when the detail page is unreachable."""
        title, company = self._split_title(listing_title)
        slug = path.rsplit("/", 1)[-1]
        return RawJob(
            source=self.name,
            source_id=slug,
            company=company,
            title=title,
            location="Kenya",
            apply_url=url,
        )
