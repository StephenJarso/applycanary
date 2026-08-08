"""Himalayas public jobs API.

    GET https://himalayas.app/jobs/api?limit=N&offset=N

Remote-only board with structured salary, like Jobicy.

Two quirks worth knowing. There is no numeric id — `guid` is the canonical URL,
so it doubles as the source identifier. And `locationRestrictions` is a list of
countries a candidate must be eligible in, not an office location; an empty
list means genuinely worldwide.
"""

from __future__ import annotations

import logging
from typing import Any

from app.sources.base import BaseSource, RawJob, clean_html, parse_epoch, register

log = logging.getLogger(__name__)

API = "https://himalayas.app/jobs/api"
DEFAULT_LIMIT = 50  # API rejects limits above 50.


@register
class HimalayasSource(BaseSource):
    name = "himalayas"

    def __init__(
        self,
        search_terms: list[str] | None = None,
        limit: int = DEFAULT_LIMIT,
        **kw: Any,
    ) -> None:
        super().__init__(**kw)
        self.search_terms = [t.lower() for t in (search_terms or []) if t]
        self.limit = min(limit, DEFAULT_LIMIT)

    async def fetch(self) -> list[RawJob]:
        payload = await self.get_json(API, params={"limit": self.limit})
        if not isinstance(payload, dict):
            raise ValueError("himalayas: expected a JSON object")

        jobs: list[RawJob] = []
        for item in payload.get("jobs") or []:
            if not isinstance(item, dict):
                continue
            try:
                job = self._parse(item)
            except Exception as exc:  # noqa: BLE001
                log.warning("himalayas: skipped job: %s", exc)
                continue
            if self._matches(job):
                jobs.append(job)
        return jobs

    def _matches(self, job: RawJob) -> bool:
        if not self.search_terms:
            return True
        haystack = f"{job.title} {job.extra.get('categories', '')}".lower()
        return any(term in haystack for term in self.search_terms)

    def _parse(self, item: dict) -> RawJob:
        restrictions = item.get("locationRestrictions")
        location = (
            ", ".join(str(r) for r in restrictions if r)
            if isinstance(restrictions, list) and restrictions
            else "Worldwide"
        )

        categories = item.get("categories")
        category_text = (
            ", ".join(str(c) for c in categories if c) if isinstance(categories, list) else ""
        )

        lo = _positive_int(item.get("minSalary"))
        hi = _positive_int(item.get("maxSalary"))
        url = str(item.get("applicationLink") or item.get("guid") or "")

        return RawJob(
            source=self.name,
            # No numeric id; guid is the canonical URL and is stable.
            source_id=str(item.get("guid") or url),
            company=str(item.get("companyName") or "").strip(),
            title=str(item.get("title") or "").strip(),
            location=location,
            description=clean_html(
                str(item.get("description") or item.get("excerpt") or "")
            ),
            apply_url=url,
            posted_at=parse_epoch(item.get("pubDate")),
            salary_min=lo,
            salary_max=hi,
            salary_currency=str(item.get("currency") or "") if (lo or hi) else "",
            salary_is_estimate=True,
            remote_flag=True,
            extra={
                "categories": category_text,
                "employment_type": str(item.get("employmentType") or ""),
                "company_slug": str(item.get("companySlug") or ""),
            },
        )


def _positive_int(value: Any) -> int | None:
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        return None
    try:
        parsed = int(float(value))
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None
