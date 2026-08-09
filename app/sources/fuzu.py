"""Fuzu Kenya — HTML scraper with JSON-LD JobPosting.

    https://www.fuzu.com/kenya/jobs

The listing page embeds a JSON-LD ``ItemList`` with job URLs. Each detail page
carries a full ``JobPosting`` schema with structured fields (identifier, title,
description, hiringOrganization, jobLocation, etc.).

This is an HTML scraper and **will break if Fuzu changes its schema or removes
JSON-LD**. Mark ``is_scraper = True`` so the verify script can flag it.
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

BASE_URL = "https://www.fuzu.com"
MAX_PAGES = 3
_JSON_LD_RE = re.compile(
    r'<script[^>]*type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
    re.DOTALL,
)


@register
class FuzuSource(BaseSource):
    name = "fuzu"
    is_scraper = True

    def __init__(
        self,
        search_terms: list[str] | None = None,
        country: str = "kenya",
        **kw: Any,
    ) -> None:
        super().__init__(**kw)
        self.search_terms = [t.lower() for t in (search_terms or []) if t]
        self.country = country

    async def fetch(self) -> list[RawJob]:
        detail_urls: list[str] = []
        for page in range(1, MAX_PAGES + 1):
            try:
                resp = await self.client.get(
                    f"{BASE_URL}/{self.country}/jobs",
                    params={"page": page},
                    headers={"Accept": "text/html"},
                )
                resp.raise_for_status()
                urls = self._extract_listing_urls(resp.text)
                detail_urls.extend(urls)
            except Exception as exc:  # noqa: BLE001
                log.warning("fuzu: listing page %d failed: %s", page, exc)

        # Deduplicate while preserving order
        seen: set[str] = set()
        unique_urls: list[str] = []
        for u in detail_urls:
            if u not in seen:
                seen.add(u)
                unique_urls.append(u)

        if not unique_urls:
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
                    log.warning("fuzu: detail %s failed: %s", url, exc)
                    return None

        results = await asyncio.gather(*(_fetch_detail(u) for u in unique_urls))
        for job in results:
            if job is not None and self._matches(job):
                jobs.append(job)
        return jobs

    def _extract_listing_urls(self, html: str) -> list[str]:
        """Pull job URLs from the JSON-LD ItemList on the listing page."""
        for script in _JSON_LD_RE.findall(html):
            try:
                data = json.loads(script)
            except (json.JSONDecodeError, ValueError):
                continue
            if isinstance(data, dict) and data.get("@type") == "ItemList":
                urls = []
                for item in data.get("itemListElement", []):
                    if isinstance(item, dict) and item.get("url"):
                        urls.append(str(item["url"]))
                return urls
        # Fallback: regex for job links
        return list(set(re.findall(
            rf'href="(/{re.escape(self.country)}/jobs/[^"]+)"', html
        )))

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
            if not isinstance(data, dict) or data.get("@type") != "JobPosting":
                continue

            # Identifier
            ident = data.get("identifier")
            source_id = ""
            if isinstance(ident, dict):
                source_id = str(ident.get("value") or "")
            if not source_id:
                source_id = url.rsplit("/", 1)[-1]

            # Company
            company = ""
            hiring = data.get("hiringOrganization")
            if isinstance(hiring, dict):
                company = str(hiring.get("name") or "").strip()

            # Location
            location = self.country.title()
            job_loc = data.get("jobLocation")
            if isinstance(job_loc, dict):
                addr = job_loc.get("address")
                if isinstance(addr, dict):
                    location = str(
                        addr.get("addressLocality")
                        or addr.get("addressRegion")
                        or location
                    )

            return RawJob(
                source=self.name,
                source_id=source_id,
                company=company,
                title=str(data.get("title") or "").strip(),
                location=location,
                description=clean_html(str(data.get("description") or "")),
                apply_url=url,
                posted_at=parse_iso(data.get("datePosted")),
                extra={
                    "industry": str(data.get("industry") or ""),
                    "employment_type": str(data.get("employmentType") or ""),
                    "skills": str(data.get("skills") or ""),
                },
            )
        return None
