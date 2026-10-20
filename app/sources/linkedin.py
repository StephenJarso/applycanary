"""LinkedIn public jobs connector.

LinkedIn has no free public jobs API. The guest jobs pages that unauthenticated
browsers use still expose a handful of search results per request, so this
connector treats them like any other scraper: polite request counts, per-detail
failure isolation, and no attempt to log in or bypass a challenge.

Only publicly rendered fields are read. `applicants` is populated when LinkedIn
prints a count; otherwise it stays NULL rather than being guessed.
"""

from __future__ import annotations

import asyncio
import html
import logging
import re
from typing import Any
from urllib.parse import urlparse

from app.sources.base import BaseSource, RawJob, clean_html, register
from app.sources.greenhouse import parse_iso

log = logging.getLogger(__name__)

SEARCH_URL = "https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search"
DETAIL_URL = "https://www.linkedin.com/jobs-guest/jobs/api/jobPosting/{job_id}"
MAX_QUERIES = 3
MAX_DETAILS = 12
DETAIL_CONCURRENCY = 4
DETAIL_TIMEOUT = 10.0

_CARD_SPLIT = re.compile(r"(?=<li\b)", re.IGNORECASE)
_TITLE_RE = re.compile(
    r'class="[^"]*base-search-card__title[^"]*"[^>]*>(.*?)</h3>',
    re.IGNORECASE | re.DOTALL,
)
_COMPANY_RE = re.compile(
    r'class="[^"]*base-search-card__subtitle[^"]*"[^>]*>(.*?)</h4>',
    re.IGNORECASE | re.DOTALL,
)
_LOCATION_RE = re.compile(
    r'class="[^"]*job-search-card__location[^"]*"[^>]*>(.*?)(?:</span>|</div>)',
    re.IGNORECASE | re.DOTALL,
)
_SALARY_RE = re.compile(
    r'class="[^"]*job-search-card__salary-info[^"]*"[^>]*>(.*?)</span>',
    re.IGNORECASE | re.DOTALL,
)
_TIME_RE = re.compile(r'<time[^>]*datetime="([^"]+)"', re.IGNORECASE)
_HREF_RE = re.compile(r'href="([^"]*?/jobs/view/[^"]+)"', re.IGNORECASE)
_ID_RE = re.compile(r"/jobs/view/(?:[^/?#]*-)?(\d{5,})")
_DESC_RE = re.compile(
    r'class="[^"]*(?:description__text|show-more-less-html__markup)[^"]*"[^>]*>'
    r'(.*?)</div>',
    re.IGNORECASE | re.DOTALL,
)
_APPLICANT_RE = re.compile(r"(\d[\d,]*)\s+applicants?\b", re.IGNORECASE)
_CURRENCY = {"$": "USD", "€": "EUR", "£": "GBP", "₹": "INR", "¥": "JPY"}


@register
class LinkedInSource(BaseSource):
    name = "linkedin"
    is_scraper = True

    def __init__(
        self,
        search_terms: list[str] | None = None,
        location: str = "",
        max_results: int = 25,
        max_queries: int = MAX_QUERIES,
        **kw: Any,
    ) -> None:
        super().__init__(**kw)
        self.search_terms = [str(t).strip() for t in (search_terms or []) if str(t).strip()]
        self.location = location.strip()
        self.max_results = max(1, min(int(max_results), 50))
        self.max_queries = max(1, min(int(max_queries), MAX_QUERIES))

    async def fetch(self) -> list[RawJob]:
        terms = self.search_terms or ["software engineer"]
        found: dict[str, RawJob] = {}
        for term in terms[: self.max_queries]:
            params: dict[str, str] = {
                "keywords": term,
                "start": "0",
                # Last 30 days: old LinkedIn results churn forever otherwise.
                "f_TPR": "r2592000",
            }
            if self.location:
                params["location"] = self.location
            resp = await self.client.get(
                SEARCH_URL,
                params=params,
                headers={"Accept": "text/html"},
            )
            if resp.status_code in (429, 999):
                raise RuntimeError(
                    f"linkedin rate-limited the guest endpoint ({resp.status_code})"
                )
            resp.raise_for_status()
            for job in _parse_cards(resp.text):
                found.setdefault(job.source_id, job)
                if len(found) >= self.max_results:
                    break
            if len(found) >= self.max_results:
                break

        jobs = list(found.values())[: self.max_results]
        if jobs:
            await self._enrich(jobs[:MAX_DETAILS])
        return jobs

    async def _enrich(self, jobs: list[RawJob]) -> None:
        """Fill description/applicants from public detail pages, best effort."""
        sem = asyncio.Semaphore(DETAIL_CONCURRENCY)

        async def one(job: RawJob) -> None:
            async with sem:
                try:
                    resp = await self.client.get(
                        DETAIL_URL.format(job_id=job.source_id),
                        headers={"Accept": "text/html"},
                        timeout=DETAIL_TIMEOUT,
                    )
                    if resp.status_code != 200:
                        return
                    detail = resp.text
                except Exception as exc:  # noqa: BLE001 - card data is still useful
                    log.debug("linkedin: detail %s failed: %s", job.source_id, exc)
                    return
            description = _parse_description(detail)
            if description and len(description) > len(job.description):
                job.description = description
            applicants = _parse_applicants(detail)
            if applicants is not None:
                job.applicants = applicants
            if not job.salary_min:
                low, high, currency = _parse_salary(
                    _text(_first(_SALARY_RE, detail))
                )
                job.salary_min, job.salary_max, job.salary_currency = low, high, currency

        await asyncio.gather(*(one(job) for job in jobs))


def _parse_cards(page: str) -> list[RawJob]:
    """Extract one RawJob per public search card."""
    jobs: list[RawJob] = []
    seen: set[str] = set()
    for chunk in _CARD_SPLIT.split(page):
        if "base-search-card" not in chunk or "base-search-card__title" not in chunk:
            continue

        href = _first(_HREF_RE, chunk)
        url = html.unescape(href).split("?")[0] if href else ""
        job_id = _job_id(url)
        if not job_id or job_id in seen:
            continue
        seen.add(job_id)

        title = _text(_first(_TITLE_RE, chunk))
        if not title:
            continue
        salary_text = _text(_first(_SALARY_RE, chunk))
        low, high, currency = _parse_salary(salary_text)
        jobs.append(RawJob(
            source="linkedin",
            source_id=job_id,
            company=_text(_first(_COMPANY_RE, chunk)),
            title=title,
            location=_text(_first(_LOCATION_RE, chunk)),
            apply_url=url or f"https://www.linkedin.com/jobs/view/{job_id}",
            posted_at=parse_iso(_first(_TIME_RE, chunk)),
            salary_min=low,
            salary_max=high,
            salary_currency=currency,
            applicants=_parse_applicants(chunk),
            extra={"salary_note": salary_text} if salary_text else {},
        ))
    return jobs


def _parse_description(detail: str) -> str:
    return clean_html(_first(_DESC_RE, detail))


def _parse_applicants(text: str) -> int | None:
    match = _APPLICANT_RE.search(text)
    if not match:
        return None
    try:
        return int(match.group(1).replace(",", ""))
    except ValueError:
        return None


def _parse_salary(text: str) -> tuple[int | None, int | None, str]:
    """Best-effort range from a card's salary line. Never used to reject a job."""
    if not text:
        return None, None, ""
    currency = next((code for symbol, code in _CURRENCY.items() if symbol in text), "")
    numbers: list[int] = []
    for raw, suffix in re.findall(r"([\d][\d,.]*)\s*([kK])?", text):
        cleaned = raw.replace(",", "").rstrip(".")
        try:
            value = float(cleaned)
        except ValueError:
            continue
        if suffix:
            value *= 1000
        if value >= 1000:
            numbers.append(int(value))
    if not numbers:
        return None, None, currency
    low = min(numbers)
    high = max(numbers) if len(numbers) > 1 else low
    return low, high, currency


def _job_id(url: str) -> str:
    match = _ID_RE.search(url)
    if match:
        return match.group(1)
    try:
        path = urlparse(url).path.rstrip("/")
    except ValueError:
        path = ""
    return path.rsplit("-", 1)[-1] if path else ""


def _first(pattern: re.Pattern[str], text: str) -> str:
    match = pattern.search(text)
    return match.group(1) if match else ""


def _text(fragment: str | None) -> str:
    if not fragment:
        return ""
    return clean_html(re.sub(r"<[^>]+>", " ", fragment))
