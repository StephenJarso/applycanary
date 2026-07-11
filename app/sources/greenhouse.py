"""Greenhouse public job board API.

    GET https://boards-api.greenhouse.io/v1/boards/{token}/jobs?content=true

`content=true` returns the full HTML description in one call, avoiding an extra
request per posting.

Submission is deliberately not implemented: Greenhouse's application endpoint
authenticates with the *employer's* API key, so there is no sanctioned path for a
third-party agent. These jobs route to the review queue.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from app.sources.base import BaseSource, RawJob, clean_html, register

log = logging.getLogger(__name__)

API = "https://boards-api.greenhouse.io/v1/boards/{token}/jobs"


def parse_iso(value: Any) -> datetime | None:
    """Parse an ISO-8601 timestamp to naive UTC, tolerating a trailing Z."""
    if not value or not isinstance(value, str):
        return None
    try:
        cleaned = value.strip().replace("Z", "+00:00")
        dt = datetime.fromisoformat(cleaned)
    except ValueError:
        return None
    if dt.tzinfo is not None:
        from datetime import timezone

        dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt


@register
class GreenhouseSource(BaseSource):
    name = "greenhouse"

    def __init__(self, board_token: str, company_name: str = "", **kw: Any) -> None:
        super().__init__(**kw)
        self.board_token = board_token
        # Boards rarely echo a display name, so accept one from companies.yaml.
        self.company_name = company_name or board_token.replace("-", " ").title()

    async def fetch(self) -> list[RawJob]:
        payload = await self.get_json(
            API.format(token=self.board_token), params={"content": "true"}
        )
        if not isinstance(payload, dict):
            raise ValueError(f"greenhouse/{self.board_token}: unexpected payload type")

        jobs: list[RawJob] = []
        for item in payload.get("jobs") or []:
            if not isinstance(item, dict):
                continue
            try:
                jobs.append(self._parse(item))
            except Exception as exc:  # noqa: BLE001 - one bad row must not kill the poll
                log.warning("greenhouse/%s: skipped job: %s", self.board_token, exc)
        return jobs

    def _parse(self, item: dict) -> RawJob:
        offices = item.get("offices") or []
        location = (item.get("location") or {}).get("name") or ""
        if not location and offices:
            location = ", ".join(
                str(o.get("name")) for o in offices if isinstance(o, dict) and o.get("name")
            )

        # Greenhouse exposes structured metadata; department is useful context
        # but is not treated as a location.
        departments = [
            str(d.get("name"))
            for d in (item.get("departments") or [])
            if isinstance(d, dict) and d.get("name")
        ]

        return RawJob(
            source=self.name,
            source_id=str(item.get("id") or ""),
            company=self.company_name,
            title=str(item.get("title") or "").strip(),
            location=location,
            description=clean_html(str(item.get("content") or "")),
            apply_url=str(item.get("absolute_url") or ""),
            posted_at=parse_iso(item.get("updated_at") or item.get("first_published")),
            ats_platform=self.name,
            ats_board_token=self.board_token,
            extra={"departments": departments},
        )
