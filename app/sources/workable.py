"""Workable public job board widget.

    GET https://apply.workable.com/api/v1/widget/accounts/{token}?details=true

Per-company board, like Greenhouse and Lever, so it takes a board token from
`config/companies.yaml` rather than search terms.

`?details=true` is what makes this useful — without it the payload omits
descriptions, and a posting with no description cannot be scored or ATS-checked.

Submission is not implemented. Workable's apply endpoint requires the employer's
own API token, so there is no sanctioned third-party path; these jobs route to
the review queue with `application_url` pre-filled, which is the same treatment
Greenhouse gets.

Note that a reachable board is not the same as a populated one: most accounts
return `jobs: []` when they have no live openings, which is a valid response and
not an error.
"""

from __future__ import annotations

import logging
from typing import Any

from app.sources.base import BaseSource, RawJob, clean_html, register
from app.sources.greenhouse import parse_iso

log = logging.getLogger(__name__)

API = "https://apply.workable.com/api/v1/widget/accounts/{token}"


@register
class WorkableSource(BaseSource):
    name = "workable"

    def __init__(self, board_token: str, company_name: str = "", **kw: Any) -> None:
        super().__init__(**kw)
        if not board_token:
            raise ValueError("workable: board_token is required")
        self.board_token = board_token
        self.company_name = company_name or board_token

    async def fetch(self) -> list[RawJob]:
        url = API.format(token=self.board_token)
        payload = await self.get_json(url, params={"details": "true"})
        if not isinstance(payload, dict):
            raise ValueError(f"workable/{self.board_token}: expected a JSON object")

        # The board reports its own display name; prefer it over the token.
        company = str(payload.get("name") or "").strip() or self.company_name

        jobs: list[RawJob] = []
        for item in payload.get("jobs") or []:
            if not isinstance(item, dict):
                continue
            try:
                jobs.append(self._parse(item, company))
            except Exception as exc:  # noqa: BLE001
                log.warning("workable/%s: skipped job: %s", self.board_token, exc)
        return jobs

    def _parse(self, item: dict, company: str) -> RawJob:
        location = ", ".join(
            part
            for part in (
                str(item.get("city") or "").strip(),
                str(item.get("state") or "").strip(),
                str(item.get("country") or "").strip(),
            )
            if part
        )

        return RawJob(
            source=self.name,
            # `shortcode` is the stable public identifier and appears in the URL.
            source_id=str(item.get("shortcode") or ""),
            company=company,
            title=str(item.get("title") or "").strip(),
            location=location,
            description=clean_html(str(item.get("description") or "")),
            # application_url lands directly on the form; url is the listing.
            apply_url=str(
                item.get("application_url") or item.get("url") or item.get("shortlink") or ""
            ),
            posted_at=parse_iso(item.get("published_on") or item.get("created_at")),
            remote_flag=bool(item["telecommuting"]) if "telecommuting" in item else None,
            ats_platform=self.name,
            ats_board_token=self.board_token,
            extra={
                "department": str(item.get("department") or ""),
                "employment_type": str(item.get("employment_type") or ""),
                "experience": str(item.get("experience") or ""),
                "industry": str(item.get("industry") or ""),
            },
        )
