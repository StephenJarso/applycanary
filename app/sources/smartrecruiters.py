"""SmartRecruiters public Posting API.

    GET  https://api.smartrecruiters.com/v1/companies/{company}/postings
    GET  https://api.smartrecruiters.com/v1/companies/{company}/postings/{id}
    POST https://api.smartrecruiters.com/v1/companies/{company}/postings/{id}/candidates

Notable because the candidate endpoint is genuinely public: it accepts an
application without the employer's credentials. That makes SmartRecruiters the
one platform this MVP can auto-submit to. The submitter lives in
`app/apply/smartrecruiters.py`; this module only ingests.

The list endpoint returns summaries, so the full description needs a per-posting
detail call. That is rate-limit sensitive, hence the concurrency cap below.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from app.sources.base import BaseSource, RawJob, clean_html, register
from app.sources.greenhouse import parse_iso

log = logging.getLogger(__name__)

LIST_API = "https://api.smartrecruiters.com/v1/companies/{company}/postings"
DETAIL_API = "https://api.smartrecruiters.com/v1/companies/{company}/postings/{posting_id}"

PAGE_LIMIT = 100
MAX_PAGES = 5
# Detail fetches run concurrently but bounded; an unbounded gather would trip
# the public rate limit and get the whole poll throttled.
DETAIL_CONCURRENCY = 5


@register
class SmartRecruitersSource(BaseSource):
    name = "smartrecruiters"

    def __init__(self, board_token: str, company_name: str = "", **kw: Any) -> None:
        super().__init__(**kw)
        self.board_token = board_token
        self.company_name = company_name or board_token.replace("-", " ").title()

    async def fetch(self) -> list[RawJob]:
        summaries = await self._list_postings()
        jobs = await self._hydrate(summaries)
        return jobs

    async def _list_postings(self) -> list[dict]:
        found: list[dict] = []
        offset = 0
        for _ in range(MAX_PAGES):
            payload = await self.get_json(
                LIST_API.format(company=self.board_token),
                params={"limit": PAGE_LIMIT, "offset": offset},
            )
            if not isinstance(payload, dict):
                raise ValueError(f"smartrecruiters/{self.board_token}: unexpected payload")
            content = payload.get("content") or []
            if not isinstance(content, list) or not content:
                break
            found.extend(item for item in content if isinstance(item, dict))
            total = payload.get("totalFound")
            offset += len(content)
            if isinstance(total, int) and offset >= total:
                break
            if len(content) < PAGE_LIMIT:
                break
        return found

    async def _hydrate(self, summaries: list[dict]) -> list[RawJob]:
        sem = asyncio.Semaphore(DETAIL_CONCURRENCY)

        async def one(summary: dict) -> RawJob | None:
            posting_id = str(summary.get("id") or "")
            if not posting_id:
                return None
            detail: dict = {}
            async with sem:
                try:
                    fetched = await self.get_json(
                        DETAIL_API.format(company=self.board_token, posting_id=posting_id)
                    )
                    if isinstance(fetched, dict):
                        detail = fetched
                except Exception as exc:  # noqa: BLE001
                    # Fall back to the summary: a job with a thin description is
                    # still worth surfacing, and the apply URL is what matters.
                    log.debug("smartrecruiters: detail fetch failed for %s: %s", posting_id, exc)
            try:
                return self._parse(summary, detail)
            except Exception as exc:  # noqa: BLE001
                log.warning("smartrecruiters/%s: skipped %s: %s", self.board_token, posting_id, exc)
                return None

        results = await asyncio.gather(*(one(s) for s in summaries))
        return [job for job in results if job is not None]

    def _parse(self, summary: dict, detail: dict) -> RawJob:
        location = _location(summary.get("location") or detail.get("location") or {})
        company = (
            (summary.get("company") or {}).get("name")
            or (detail.get("company") or {}).get("name")
            or self.company_name
        )

        description = _description(detail)
        apply_url = (
            str(summary.get("ref") or "")
            or str(detail.get("applyUrl") or "")
            or f"https://jobs.smartrecruiters.com/{self.board_token}/{summary.get('id')}"
        )

        remote = None
        loc_obj = summary.get("location") or {}
        if isinstance(loc_obj, dict) and "remote" in loc_obj:
            remote = bool(loc_obj.get("remote"))

        return RawJob(
            source=self.name,
            source_id=str(summary.get("id") or ""),
            company=str(company),
            title=str(summary.get("name") or detail.get("name") or "").strip(),
            location=location,
            description=description,
            apply_url=apply_url,
            posted_at=parse_iso(summary.get("releasedDate") or detail.get("releasedDate")),
            remote_flag=remote,
            ats_platform=self.name,
            ats_board_token=self.board_token,
            extra={"uuid": str(summary.get("uuid") or "")},
        )


def _location(loc: Any) -> str:
    if not isinstance(loc, dict):
        return ""
    parts = [
        str(loc.get(key) or "")
        for key in ("city", "region", "country")
        if loc.get(key)
    ]
    if loc.get("remote") and not parts:
        return "Remote"
    return ", ".join(p for p in parts if p)


def _description(detail: dict) -> str:
    """Flatten SmartRecruiters' nested jobAd sections into one text blob."""
    ad = detail.get("jobAd")
    if not isinstance(ad, dict):
        return clean_html(str(detail.get("jobDescription") or ""))
    sections = (ad.get("sections") or {}) if isinstance(ad.get("sections"), dict) else {}
    ordered = ("companyDescription", "jobDescription", "qualifications", "additionalInformation")
    chunks: list[str] = []
    for key in ordered:
        block = sections.get(key)
        if isinstance(block, dict):
            title = str(block.get("title") or "")
            text = clean_html(str(block.get("text") or ""))
            if text:
                chunks.append(f"{title}\n{text}".strip() if title else text)
    return "\n\n".join(chunks)
