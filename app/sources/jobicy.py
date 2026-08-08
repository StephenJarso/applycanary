"""Jobicy public JSON API.

    GET https://jobicy.com/api/v2/remote-jobs?count=N

Remote-only board. Notable for returning *structured* annual salary
(`annualSalaryMin` / `annualSalaryMax` / `salaryCurrency`) rather than prose,
so unlike the other aggregators these values are real enough to drive the
salary floor filter. They are still marked as estimates: the figures are
self-reported by employers and frequently absent.

`jobGeo` is an eligibility region ("USA", "Anywhere"), not an office location.
"""

from __future__ import annotations

import logging
from typing import Any

from app.sources.base import BaseSource, RawJob, clean_html, register
from app.sources.greenhouse import parse_iso

log = logging.getLogger(__name__)

API = "https://jobicy.com/api/v2/remote-jobs"
DEFAULT_COUNT = 50  # API caps this; asking for more is silently truncated.


@register
class JobicySource(BaseSource):
    name = "jobicy"

    def __init__(
        self,
        search_terms: list[str] | None = None,
        industry: str = "",
        count: int = DEFAULT_COUNT,
        **kw: Any,
    ) -> None:
        super().__init__(**kw)
        self.search_terms = [t.lower() for t in (search_terms or []) if t]
        self.industry = industry
        self.count = count

    async def fetch(self) -> list[RawJob]:
        params: dict[str, Any] = {"count": self.count}
        if self.industry:
            params["industry"] = self.industry

        payload = await self.get_json(API, params=params)
        if not isinstance(payload, dict):
            raise ValueError("jobicy: expected a JSON object")

        jobs: list[RawJob] = []
        for item in payload.get("jobs") or []:
            if not isinstance(item, dict):
                continue
            try:
                job = self._parse(item)
            except Exception as exc:  # noqa: BLE001
                log.warning("jobicy: skipped job: %s", exc)
                continue
            if self._matches(job):
                jobs.append(job)
        return jobs

    def _matches(self, job: RawJob) -> bool:
        if not self.search_terms:
            return True
        haystack = f"{job.title} {job.extra.get('industry', '')}".lower()
        return any(term in haystack for term in self.search_terms)

    def _parse(self, item: dict) -> RawJob:
        lo = _positive_int(item.get("annualSalaryMin"))
        hi = _positive_int(item.get("annualSalaryMax"))

        def joined(key: str) -> str:
            value = item.get(key)
            if isinstance(value, list):
                return ", ".join(str(v) for v in value if v)
            return str(value or "")

        return RawJob(
            source=self.name,
            source_id=str(item.get("id") or ""),
            company=str(item.get("companyName") or "").strip(),
            title=str(item.get("jobTitle") or "").strip(),
            location=str(item.get("jobGeo") or "Remote"),
            description=clean_html(
                str(item.get("jobDescription") or item.get("jobExcerpt") or "")
            ),
            apply_url=str(item.get("url") or ""),
            posted_at=parse_iso(item.get("pubDate")),
            salary_min=lo,
            salary_max=hi,
            salary_currency=str(item.get("salaryCurrency") or "") if (lo or hi) else "",
            # Employer-reported and often missing; never reject a job on it.
            salary_is_estimate=True,
            remote_flag=True,
            extra={
                "industry": joined("jobIndustry"),
                "job_type": joined("jobType"),
                "level": str(item.get("jobLevel") or ""),
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
