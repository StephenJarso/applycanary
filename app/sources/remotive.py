"""Remotive public JSON feed.

    GET https://remotive.com/api/remote-jobs?limit=N

Curated remote-only board, so every posting is treated as remote regardless of
what `candidate_required_location` says — that field carries eligibility
("Europe", "USA only"), not an office address.

`salary` is a free-text string ("$50k - $70k", "competitive", or empty), so it
is passed through as a note rather than parsed. Guessing a number from prose
would feed the salary floor filter bad data and silently drop real jobs.
"""

from __future__ import annotations

import logging
from typing import Any

from app.sources.base import BaseSource, RawJob, clean_html, register
from app.sources.greenhouse import parse_iso

log = logging.getLogger(__name__)

API = "https://remotive.com/api/remote-jobs"
DEFAULT_LIMIT = 200


@register
class RemotiveSource(BaseSource):
    name = "remotive"

    def __init__(
        self,
        search_terms: list[str] | None = None,
        category: str = "",
        limit: int = DEFAULT_LIMIT,
        **kw: Any,
    ) -> None:
        super().__init__(**kw)
        self.search_terms = [t.lower() for t in (search_terms or []) if t]
        self.category = category
        self.limit = limit

    async def fetch(self) -> list[RawJob]:
        params: dict[str, Any] = {"limit": self.limit}
        # Remotive supports server-side category filtering, which is cheaper
        # than pulling everything and discarding most of it.
        if self.category:
            params["category"] = self.category

        payload = await self.get_json(API, params=params)
        if not isinstance(payload, dict):
            raise ValueError("remotive: expected a JSON object")

        jobs: list[RawJob] = []
        for item in payload.get("jobs") or []:
            if not isinstance(item, dict):
                continue
            try:
                job = self._parse(item)
            except Exception as exc:  # noqa: BLE001
                log.warning("remotive: skipped job: %s", exc)
                continue
            if self._matches(job):
                jobs.append(job)
        return jobs

    def _matches(self, job: RawJob) -> bool:
        if not self.search_terms:
            return True
        haystack = f"{job.title} {job.extra.get('tags', '')}".lower()
        return any(term in haystack for term in self.search_terms)

    def _parse(self, item: dict) -> RawJob:
        tags = item.get("tags")
        tag_text = ", ".join(str(t) for t in tags if t) if isinstance(tags, list) else ""

        return RawJob(
            source=self.name,
            source_id=str(item.get("id") or ""),
            company=str(item.get("company_name") or "").strip(),
            title=str(item.get("title") or "").strip(),
            # Eligibility region, not an office. Kept for the UI; is_remote is
            # forced True below.
            location=str(item.get("candidate_required_location") or "Remote"),
            description=clean_html(str(item.get("description") or "")),
            apply_url=str(item.get("url") or ""),
            posted_at=parse_iso(item.get("publication_date")),
            remote_flag=True,
            extra={
                "tags": tag_text,
                "job_type": str(item.get("job_type") or ""),
                "category": str(item.get("category") or ""),
                # Free text; surfaced rather than parsed into salary_min/max.
                "salary_note": str(item.get("salary") or ""),
            },
        )
