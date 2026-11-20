"""AfricaHacks hackathon and opportunity feed.

Fetches the AfricaHacks public RSS feed for hackathon announcements,
challenges, and Africa-focused tech opportunities.
"""
from __future__ import annotations

import logging
import xml.etree.ElementTree as ET
from typing import Any

from app.sources.base import BaseSource, RawJob, register

log = logging.getLogger(__name__)

FEED_URL = "https://africahacks.com/feed/"


@register
class AfricaHacksSource(BaseSource):
    name = "africahacks"
    is_scraper = True

    def __init__(self, **kw: Any) -> None:
        super().__init__(**kw)

    async def fetch(self) -> list[RawJob]:
        try:
            resp = await self.client.get(
                FEED_URL, headers={"Accept": "application/rss+xml, text/xml"}
            )
            resp.raise_for_status()
        except Exception as exc:  # noqa: BLE001
            log.warning("africahacks: feed fetch failed: %s", exc)
            return []

        try:
            root = ET.fromstring(resp.text)
        except ET.ParseError as exc:
            log.warning("africahacks: XML parse error: %s", exc)
            return []

        jobs: list[RawJob] = []
        for item in root.findall(".//item")[:20]:
            title = (item.findtext("title") or "").strip()
            link = (item.findtext("link") or "").strip()
            desc = (item.findtext("description") or "").strip()
            if not title or not link:
                continue
            jobs.append(RawJob(
                source="africahacks",
                source_id=link,
                company="AfricaHacks",
                title=title,
                location="Africa (Online)",
                apply_url=link,
                description=desc,
                is_remote=True,
            ))
        log.info("africahacks: fetched %d items", len(jobs))
        return jobs
