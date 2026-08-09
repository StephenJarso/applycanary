"""CareerPoint Kenya — WordPress RSS feed.

    GET https://www.careerpointkenya.co.ke/feed/

Lightweight connector that reads the standard WordPress RSS 2.0 feed. No HTML
scraping is involved; this is the most stable of the Kenyan connectors because
the feed format is a WordPress default unlikely to change.

Titles frequently follow ``Job Title Company Location, Kenya``, which is
parsed best-effort to separate title and company.

Returns ~30 items per fetch. There is no pagination endpoint.
"""

from __future__ import annotations

import logging
import xml.etree.ElementTree as ET
from datetime import UTC
from email.utils import parsedate_to_datetime
from typing import Any

from app.sources.base import BaseSource, RawJob, clean_html, register

log = logging.getLogger(__name__)

FEED_URL = "https://www.careerpointkenya.co.ke/feed/"
# WordPress content:encoded namespace
_NS_CONTENT = "{http://purl.org/rss/1.0/modules/content/}"


@register
class CareerPointKenyaSource(BaseSource):
    name = "careerpointkenya"
    is_scraper = False

    def __init__(
        self,
        search_terms: list[str] | None = None,
        **kw: Any,
    ) -> None:
        super().__init__(**kw)
        self.search_terms = [t.lower() for t in (search_terms or []) if t]

    async def fetch(self) -> list[RawJob]:
        try:
            resp = await self.client.get(
                FEED_URL,
                headers={"Accept": "application/rss+xml, application/xml, text/xml"},
            )
        except Exception as err:
            if "CERTIFICATE_VERIFY_FAILED" in str(err) or "SSLError" in type(err).__name__:
                import httpx

                async with httpx.AsyncClient(verify=False, timeout=20.0) as insecure_client:
                    resp = await insecure_client.get(
                        FEED_URL,
                        headers={"Accept": "application/rss+xml, application/xml, text/xml"},
                    )
            else:
                raise
        resp.raise_for_status()

        root = ET.fromstring(resp.text)
        channel = root.find("channel")
        if channel is None:
            raise ValueError("careerpointkenya: no <channel> in RSS feed")

        jobs: list[RawJob] = []
        for item in channel.findall("item"):
            try:
                job = self._parse(item)
            except Exception as exc:  # noqa: BLE001
                log.warning("careerpointkenya: skipped item: %s", exc)
                continue
            if self._matches(job):
                jobs.append(job)
        return jobs

    def _matches(self, job: RawJob) -> bool:
        if not self.search_terms:
            return True
        haystack = job.title.lower()
        return any(term in haystack for term in self.search_terms)

    def _parse(self, item: ET.Element) -> RawJob:
        title = (item.findtext("title") or "").strip()
        link = (item.findtext("link") or "").strip()
        pub_date = item.findtext("pubDate")

        # Prefer full content over summary
        content = item.findtext(f"{_NS_CONTENT}encoded") or ""
        if not content:
            content = item.findtext("description") or ""
        description = clean_html(content)

        # Parse date
        posted_at = None
        if pub_date:
            try:
                dt = parsedate_to_datetime(pub_date)
                # Convert to naive UTC to match the model convention
                posted_at = dt.astimezone(UTC).replace(tzinfo=None)
            except (ValueError, TypeError):
                pass

        # Best-effort company extraction from title patterns like:
        #   "Job Title Company Location, Kenya"
        #   "Job Title at Company"
        company = ""
        for sep in (" at ", " At "):
            if sep in title:
                parts = title.split(sep, 1)
                company = parts[1].strip()
                title = parts[0].strip()
                break

        if not company:
            # Check for ' Job ' pattern: 'Technical Sales Executive (Solar) Job LY Power Nairobi, Kenya'
            if " Job " in title:
                parts = title.split(" Job ", 1)
                title = parts[0].strip() + " Job"
                company = parts[1].strip()
            else:
                company = "CareerPoint Kenya"

        return RawJob(
            source=self.name,
            source_id=link,
            company=company,
            title=title,
            location="Kenya",
            description=description,
            apply_url=link,
            posted_at=posted_at,
        )
