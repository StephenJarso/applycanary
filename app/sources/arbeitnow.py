"""Arbeitnow public job board API.

    GET https://www.arbeitnow.com/api/job-board-api

European-heavy (largely German market) and the only one of the aggregators here
that returns an explicit `remote` boolean, so remoteness is read rather than
inferred.

Descriptions arrive as HTML and are frequently in German. They are flattened but
not translated; the scorer works on whatever text it is given, so a German
posting will score against German keywords and generally rank low for an
English-language resume. That is honest behaviour rather than a bug, but it is
why this source is worth pairing with a `search_terms` filter.
"""

from __future__ import annotations

import logging
from typing import Any

from app.sources.base import BaseSource, RawJob, clean_html, parse_epoch, register

log = logging.getLogger(__name__)

API = "https://www.arbeitnow.com/api/job-board-api"


@register
class ArbeitnowSource(BaseSource):
    name = "arbeitnow"

    def __init__(
        self,
        search_terms: list[str] | None = None,
        remote_only: bool = False,
        **kw: Any,
    ) -> None:
        super().__init__(**kw)
        self.search_terms = [t.lower() for t in (search_terms or []) if t]
        self.remote_only = remote_only

    async def fetch(self) -> list[RawJob]:
        payload = await self.get_json(API)
        if not isinstance(payload, dict):
            raise ValueError("arbeitnow: expected a JSON object")

        jobs: list[RawJob] = []
        for item in payload.get("data") or []:
            if not isinstance(item, dict):
                continue
            try:
                job = self._parse(item)
            except Exception as exc:  # noqa: BLE001
                log.warning("arbeitnow: skipped job: %s", exc)
                continue
            if self.remote_only and not job.remote_flag:
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
        def joined(key: str) -> str:
            value = item.get(key)
            return ", ".join(str(v) for v in value if v) if isinstance(value, list) else ""

        return RawJob(
            source=self.name,
            # `slug` is the stable identifier; there is no numeric id.
            source_id=str(item.get("slug") or ""),
            company=str(item.get("company_name") or "").strip(),
            title=str(item.get("title") or "").strip(),
            location=str(item.get("location") or ""),
            description=clean_html(str(item.get("description") or "")),
            apply_url=str(item.get("url") or ""),
            posted_at=parse_epoch(item.get("created_at")),
            remote_flag=bool(item.get("remote")) if "remote" in item else None,
            extra={"tags": joined("tags"), "job_types": joined("job_types")},
        )
