"""BrighterMonday Kenya — HTML scraper with JSON-LD JobPosting.

    https://www.brightermonday.co.ke/jobs

Parses the listing index for detail-page URLs, then reads the structured
JSON-LD ``JobPosting`` embedded in each detail page's ``@graph``. This is
more stable than scraping visible markup, but **will break if BrighterMonday
changes its schema or removes JSON-LD**.

Mark ``is_scraper = True`` so the verify script can flag it separately.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from typing import Any

from app.sources.base import BaseSource, RawJob, clean_html, register
from app.sources.greenhouse import parse_iso

log = logging.getLogger(__name__)

BASE_URL = "https://www.brightermonday.co.ke"
LISTING_INDEX = f"{BASE_URL}/jobs"
MAX_PAGES = 3
_LISTING_RE = re.compile(
    r'href="(https://www\.brightermonday\.co\.ke/listings/[^"]+)"'
)
_JSON_LD_RE = re.compile(
    r'<script[^>]*type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
    re.DOTALL,
)


@register
class BrighterMondaySource(BaseSource):
    name = "brightermonday"
    is_scraper = True

    def __init__(
        self,
        search_terms: list[str] | None = None,
        **kw: Any,
    ) -> None:
        super().__init__(**kw)
        self.search_terms = [t.lower() for t in (search_terms or []) if t]

    async def fetch(self) -> list[RawJob]:
        detail_urls: set[str] = set()
        for page in range(1, MAX_PAGES + 1):
            try:
                resp = await self.client.get(
                    LISTING_INDEX,
                    params={"page": page},
                    headers={"Accept": "text/html"},
                )
                resp.raise_for_status()
                detail_urls.update(_LISTING_RE.findall(resp.text))
            except Exception as exc:  # noqa: BLE001
                log.warning("brightermonday: page %d failed: %s", page, exc)

        if not detail_urls:
            return []

        sem = asyncio.Semaphore(3)
        jobs: list[RawJob] = []

        async def _fetch_detail(url: str) -> RawJob | None:
            async with sem:
                try:
                    resp = await self.client.get(
                        url, headers={"Accept": "text/html"}
                    )
                    resp.raise_for_status()
                    return self._parse_detail(url, resp.text)
                except Exception as exc:  # noqa: BLE001
                    log.warning("brightermonday: detail %s failed: %s", url, exc)
                    return None

        results = await asyncio.gather(*(_fetch_detail(u) for u in detail_urls))
        for job in results:
            if job is not None and self._matches(job):
                jobs.append(job)
        return jobs

    def _matches(self, job: RawJob) -> bool:
        if not self.search_terms:
            return True
        haystack = job.title.lower()
        return any(term in haystack for term in self.search_terms)

    def _parse_detail(self, url: str, html: str) -> RawJob | None:
        for script in _JSON_LD_RE.findall(html):
            try:
                data = json.loads(script)
            except (json.JSONDecodeError, ValueError):
                continue
            graph = data.get("@graph", []) if isinstance(data, dict) else []
            posting = None
            orgs: dict[str, str] = {}
            for node in graph:
                if not isinstance(node, dict):
                    continue
                if node.get("@type") == "JobPosting":
                    posting = node
                if node.get("@type") == "Organization" and node.get("@id"):
                    orgs[node["@id"]] = str(node.get("name") or "")

            if posting is None:
                continue

            # Resolve hiringOrganization
            company = ""
            hiring = posting.get("hiringOrganization")
            if isinstance(hiring, dict):
                company = str(hiring.get("name") or "")
                if not company and hiring.get("@id"):
                    company = orgs.get(hiring["@id"], "")

            # Resolve location
            location = "Kenya"
            job_loc = posting.get("jobLocation")
            if isinstance(job_loc, dict):
                addr = job_loc.get("address")
                if isinstance(addr, dict):
                    location = str(
                        addr.get("addressLocality")
                        or addr.get("addressRegion")
                        or "Kenya"
                    )

            slug = url.rsplit("/", 1)[-1] if "/" in url else url

            return RawJob(
                source=self.name,
                source_id=slug,
                company=company,
                title=str(posting.get("title") or "").strip(),
                location=location,
                description=clean_html(str(posting.get("description") or "")),
                apply_url=url,
                posted_at=parse_iso(posting.get("datePosted")),
                extra={
                    "industry": str(posting.get("industry") or ""),
                    "employment_type": str(posting.get("employmentType") or ""),
                },
            )
        return None
