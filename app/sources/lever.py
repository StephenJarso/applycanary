"""Lever public postings API.

    GET https://api.lever.co/v0/postings/{token}?mode=json

Returns full descriptions inline. Salary is not a first-class field, so it is
left unset rather than guessed from prose.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from app.sources.base import BaseSource, RawJob, clean_html, register

log = logging.getLogger(__name__)

API = "https://api.lever.co/v0/postings/{token}"


@register
class LeverSource(BaseSource):
    name = "lever"

    def __init__(self, board_token: str, company_name: str = "", **kw: Any) -> None:
        super().__init__(**kw)
        self.board_token = board_token
        self.company_name = company_name or board_token.replace("-", " ").title()

    async def fetch(self) -> list[RawJob]:
        payload = await self.get_json(
            API.format(token=self.board_token), params={"mode": "json"}
        )
        # Lever returns a bare list, unlike Greenhouse's wrapper object.
        if isinstance(payload, dict):
            payload = payload.get("data") or []
        if not isinstance(payload, list):
            raise ValueError(f"lever/{self.board_token}: unexpected payload type")

        jobs: list[RawJob] = []
        for item in payload:
            if not isinstance(item, dict):
                continue
            try:
                jobs.append(self._parse(item))
            except Exception as exc:  # noqa: BLE001
                log.warning("lever/%s: skipped job: %s", self.board_token, exc)
        return jobs

    def _parse(self, item: dict) -> RawJob:
        cats = item.get("categories") or {}
        location = str(cats.get("location") or "")
        commitment = str(cats.get("commitment") or "")

        # Prefer the plain-text description Lever already provides.
        body = item.get("descriptionPlain") or item.get("description") or ""
        extra_lists = []
        for section in item.get("lists") or []:
            if isinstance(section, dict):
                heading = str(section.get("text") or "")
                content = clean_html(str(section.get("content") or ""))
                if heading or content:
                    extra_lists.append(f"{heading}\n{content}".strip())
        description = "\n\n".join(
            part for part in [clean_html(str(body)), *extra_lists] if part
        )

        posted = None
        if isinstance(ts := item.get("createdAt"), (int, float)):
            # Lever uses epoch milliseconds.
            posted = datetime.fromtimestamp(ts / 1000, tz=timezone.utc).replace(tzinfo=None)

        workplace = str(item.get("workplaceType") or "").lower()
        remote_flag = True if workplace == "remote" else None

        return RawJob(
            source=self.name,
            source_id=str(item.get("id") or ""),
            company=self.company_name,
            title=str(item.get("text") or "").strip(),
            location=location,
            description=description,
            apply_url=str(item.get("hostedUrl") or item.get("applyUrl") or ""),
            posted_at=posted,
            remote_flag=remote_flag,
            ats_platform=self.name,
            ats_board_token=self.board_token,
            extra={"commitment": commitment, "workplace": workplace},
        )
