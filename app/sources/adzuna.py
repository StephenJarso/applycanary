"""Adzuna job search API connector.

    https://developer.adzuna.com/

Structured JSON API (no scraping), global coverage, filterable by keyword/
location/salary. Free tier: 500 requests/month.
"""

from __future__ import annotations

import logging
from typing import Any

from app.config import Settings, get_settings
from app.sources.base import BaseSource, RawJob, clean_html, register
from app.sources.greenhouse import parse_iso

log = logging.getLogger(__name__)

# Country codes Adzuna supports. Default to 'us' if not configured.
_ADZUNA_COUNTRIES = {
    "at", "au", "be", "br", "ca", "ch", "de", "es", "fr", "gb", "in",
    "it", "mx", "nl", "nz", "pl", "ru", "sg", "us", "za",
}
_DEFAULT_COUNTRY = "us"
API_BASE = "https://api.adzuna.com/v1/api/jobs"


@register
class AdzunaSource(BaseSource):
    name = "adzuna"
    # Requires ADZUNA_APP_ID and ADZUNA_APP_KEY in settings; enabled=False if absent.
    enabled = True

    def __init__(
        self,
        search_terms: list[str] | None = None,
        country: str = _DEFAULT_COUNTRY,
        location: str = "",
        max_results: int = 50,
        **kw: Any,
    ) -> None:
        super().__init__(**kw)
        self.search_terms = [t.lower() for t in (search_terms or []) if t]
        self.country = (country or _DEFAULT_COUNTRY).lower()
        self.location = location
        self.max_results = max_results

        if self.country not in _ADZUNA_COUNTRIES:
            log.warning("adzuna: unknown country %r, falling back to %s",
                        self.country, _DEFAULT_COUNTRY)
            self.country = _DEFAULT_COUNTRY

    async def fetch(self) -> list[RawJob]:
        settings = get_settings()
        if not settings.adzuna_app_id or not settings.adzuna_app_key:
            log.info("adzuna: ADZUNA_APP_ID/APP_KEY not configured, skipping")
            return []

        params = {
            "app_id": settings.adzuna_app_id,
            "app_key": settings.adzuna_app_key,
            "results_per_page": self.max_results,
            "content-type": "application/json",
        }
        if self.search_terms:
            params["what"] = " ".join(self.search_terms)
        if self.location:
            params["where"] = self.location

        url = f"{API_BASE}/{self.country}/search/1"
        payload = await self.get_json(url, params=params)
        if not isinstance(payload, dict):
            raise ValueError("adzuna: expected JSON object")

        jobs: list[RawJob] = []
        for item in payload.get("results") or []:
            if not isinstance(item, dict):
                continue
            try:
                job = self._parse(item)
            except Exception as exc:  # noqa: BLE001
                log.warning("adzuna: skipped job: %s", exc)
                continue
            if self._matches(job):
                jobs.append(job)
        return jobs

    def _matches(self, job: RawJob) -> bool:
        if not self.search_terms:
            return True
        haystack = f"{job.title} {job.description} {job.extra.get('category', '')}".lower()
        return any(term in haystack for term in self.search_terms)

    def _parse(self, item: dict) -> RawJob:
        company = str(item.get("company", {}).get("display_name", "") or "").strip()
        loc_obj = item.get("location", {})
        location = str(loc_obj.get("display_name", "") or "").strip()

        # Adzuna returns ISO timestamp in 'created'.
        posted_at = parse_iso(item.get("created"))

        salary_min = item.get("salary_min")
        salary_max = item.get("salary_max")
        salary_is_estimate = bool(item.get("salary_is_predicted"))

        category = str(item.get("category", {}).get("label", "") or "")
        contract_type = str(item.get("contract_type", "") or "")
        contract_time = str(item.get("contract_time", "") or "")

        return RawJob(
            source=self.name,
            source_id=str(item.get("id") or ""),
            company=company,
            title=str(item.get("title") or "").strip(),
            location=location,
            description=clean_html(item.get("description") or ""),
            apply_url=str(item.get("redirect_url") or ""),
            posted_at=posted_at,
            salary_min=int(salary_min) if salary_min else None,
            salary_max=int(salary_max) if salary_max else None,
            salary_currency="",
            salary_is_estimate=salary_is_estimate,
            remote_flag=None,
            extra={
                "category": category,
                "contract_type": contract_type,
                "contract_time": contract_time,
            },
        )


def _adzuna_settings(settings: Settings) -> tuple[str, str] | None:
    """Return (app_id, app_key) if both present, else None."""
    if settings.adzuna_app_id and settings.adzuna_app_key:
        return settings.adzuna_app_id, settings.adzuna_app_key
    return None
