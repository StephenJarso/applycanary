"""RemoteOK public JSON feed.

    GET https://remoteok.com/api

Aggregator, so most postings here also exist on a company's own ATS board; dedup
does the reconciling. The first element of the response is a legal notice rather
than a job and must be dropped.

Its salary fields are broad self-reported ranges, so they are flagged as
estimates and never used to reject a job outright.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

from app.sources.base import BaseSource, RawJob, clean_html, register
from app.sources.greenhouse import parse_iso

log = logging.getLogger(__name__)

API = "https://remoteok.com/api"


@register
class RemoteOkSource(BaseSource):
    name = "remoteok"

    def __init__(self, search_terms: list[str] | None = None, **kw: Any) -> None:
        super().__init__(**kw)
        # The feed is not filterable server-side, so filter client-side.
        self.search_terms = [t.lower() for t in (search_terms or []) if t]

    async def fetch(self) -> list[RawJob]:
        payload = await self.get_json(API, headers={"Accept": "application/json"})
        if not isinstance(payload, list):
            raise ValueError("remoteok: expected a JSON array")

        jobs: list[RawJob] = []
        for item in payload:
            if not isinstance(item, dict):
                continue
            # The legal disclaimer element has no id/position.
            if item.get("legal") or not item.get("id"):
                continue
            try:
                job = self._parse(item)
            except Exception as exc:  # noqa: BLE001
                log.warning("remoteok: skipped job: %s", exc)
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
        tags = item.get("tags") or []
        tag_text = ", ".join(str(t) for t in tags if t) if isinstance(tags, list) else ""

        posted = parse_iso(item.get("date"))
        if posted is None and isinstance(epoch := item.get("epoch"), (int, float)):
            posted = datetime.fromtimestamp(epoch, tz=UTC).replace(tzinfo=None)

        lo = _int_or_none(item.get("salary_min"))
        hi = _int_or_none(item.get("salary_max"))

        return RawJob(
            source=self.name,
            source_id=str(item.get("id") or ""),
            company=str(item.get("company") or "").strip(),
            title=str(item.get("position") or item.get("title") or "").strip(),
            location=str(item.get("location") or "Remote"),
            description=clean_html(str(item.get("description") or "")),
            apply_url=str(item.get("apply_url") or item.get("url") or ""),
            posted_at=posted,
            salary_min=lo,
            salary_max=hi,
            salary_currency="USD" if (lo or hi) else "",
            # Self-reported and often a wide guess; must not drive hard filters.
            salary_is_estimate=True,
            remote_flag=True,
            extra={"tags": tag_text},
        )


def _int_or_none(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)) and value > 0:
        return int(value)
    if isinstance(value, str):
        digits = "".join(ch for ch in value if ch.isdigit())
        if digits:
            parsed = int(digits)
            return parsed if parsed > 0 else None
    return None
