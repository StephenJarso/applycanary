"""Ashby public job board API.

    GET https://api.ashbyhq.com/posting-api/job-board/{token}?includeCompensation=true

Ashby is the one major board that returns structured compensation, so salary
floors filter reliably here rather than being parsed out of prose.
"""

from __future__ import annotations

import logging
import re
from typing import Any

from app.sources.base import BaseSource, RawJob, clean_html, register
from app.sources.greenhouse import parse_iso

log = logging.getLogger(__name__)

API = "https://api.ashbyhq.com/posting-api/job-board/{token}"


@register
class AshbySource(BaseSource):
    name = "ashby"

    def __init__(self, board_token: str, company_name: str = "", **kw: Any) -> None:
        super().__init__(**kw)
        self.board_token = board_token
        self.company_name = company_name or board_token.replace("-", " ").title()

    async def fetch(self) -> list[RawJob]:
        payload = await self.get_json(
            API.format(token=self.board_token),
            params={"includeCompensation": "true"},
        )
        if not isinstance(payload, dict):
            raise ValueError(f"ashby/{self.board_token}: unexpected payload type")

        jobs: list[RawJob] = []
        for item in payload.get("jobs") or []:
            if not isinstance(item, dict):
                continue
            # Ashby exposes drafts/internal roles; only take live public ones.
            if item.get("isListed") is False:
                continue
            try:
                jobs.append(self._parse(item))
            except Exception as exc:  # noqa: BLE001
                log.warning("ashby/%s: skipped job: %s", self.board_token, exc)
        return jobs

    def _parse(self, item: dict) -> RawJob:
        lo, hi, currency = _compensation(item)
        description = clean_html(
            str(item.get("descriptionHtml") or item.get("descriptionPlain") or "")
        )
        return RawJob(
            source=self.name,
            source_id=str(item.get("id") or ""),
            company=str(item.get("companyName") or "") or self.company_name,
            title=str(item.get("title") or "").strip(),
            location=str(item.get("location") or ""),
            description=description,
            apply_url=str(item.get("applyUrl") or item.get("jobUrl") or ""),
            posted_at=parse_iso(item.get("publishedAt") or item.get("updatedAt")),
            salary_min=lo,
            salary_max=hi,
            salary_currency=currency,
            remote_flag=bool(item.get("isRemote")) if "isRemote" in item else None,
            ats_platform=self.name,
            ats_board_token=self.board_token,
            extra={"department": str(item.get("department") or "")},
        )


def _compensation(item: dict) -> tuple[int | None, int | None, str]:
    """Pull an annual salary range out of Ashby's compensation block.

    Ashby nests tiers by component (salary, equity, bonus). Only salary-like
    components in an annual interval are used; equity grants and hourly rates
    would make a salary floor meaningless.
    """
    comp = item.get("compensation")
    if not isinstance(comp, dict):
        return None, None, ""

    tiers = comp.get("compensationTiers")
    if not isinstance(tiers, list):
        return None, None, ""

    lows: list[float] = []
    highs: list[float] = []
    currency = ""

    for tier in tiers:
        if not isinstance(tier, dict):
            continue
        for comp_part in tier.get("components") or []:
            if not isinstance(comp_part, dict):
                continue
            summary = str(comp_part.get("summary") or "")
            comp_type = str(comp_part.get("compensationType") or "").lower()
            interval = str(comp_part.get("interval") or "").lower()
            if comp_type and "salary" not in comp_type:
                continue
            if interval and "year" not in interval:
                continue
            lo = comp_part.get("minValue")
            hi = comp_part.get("maxValue")
            if isinstance(lo, (int, float)):
                lows.append(float(lo))
            if isinstance(hi, (int, float)):
                highs.append(float(hi))
            currency = currency or str(comp_part.get("currencyCode") or "")
            if not lows and not highs and summary:
                parsed_lo, parsed_hi, parsed_cur = _parse_summary(summary)
                if parsed_lo:
                    lows.append(parsed_lo)
                if parsed_hi:
                    highs.append(parsed_hi)
                currency = currency or parsed_cur

    if not lows and not highs:
        return None, None, currency
    return (
        int(min(lows)) if lows else None,
        int(max(highs)) if highs else None,
        currency,
    )


_MONEY = re.compile(r"([$€£])?\s*([\d][\d,]*)\s*([kK])?")


def _parse_summary(summary: str) -> tuple[float | None, float | None, str]:
    """Fallback for boards that only give a human string like '$120K - $160K'."""
    symbol_to_code = {"$": "USD", "€": "EUR", "£": "GBP"}
    values: list[float] = []
    currency = ""
    for match in _MONEY.finditer(summary):
        symbol, digits, suffix = match.groups()
        if symbol:
            currency = currency or symbol_to_code.get(symbol, "")
        try:
            value = float(digits.replace(",", ""))
        except ValueError:
            continue
        if suffix:
            value *= 1000
        # Ignore stray small numbers (percentages, years) that are not salaries.
        if value >= 1000:
            values.append(value)
    if not values:
        return None, None, currency
    return min(values), (max(values) if len(values) > 1 else None), currency
