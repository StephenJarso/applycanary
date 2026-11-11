"""Job boards added by URL at runtime.

``companies.yaml`` is a deploy-time file shared by every user. This module is
the same idea in the database: an admin pastes a board URL, it is recognised as
a Greenhouse/Lever/Ashby/Workable/SmartRecruiters board or a plain careers page,
and the existing connector for that platform polls it on the curated cadence.

Recognition is deliberately conservative. An unknown URL becomes a `careers`
source, which reads one page (or its sitemap), rather than guessing a platform
token that will 404 forever.
"""

from __future__ import annotations

import asyncio
import logging
import re
from urllib.parse import urlparse

import httpx
from sqlmodel import select

from app.db import session_scope
from app.models import CustomBoard
from app.sources.base import BaseSource, RawJob, all_sources

log = logging.getLogger(__name__)

ATS_KINDS = ("greenhouse", "lever", "ashby", "workable", "smartrecruiters")
CAREERS_KIND = "careers"
BOARD_KINDS = (*ATS_KINDS, CAREERS_KIND)

# (platform, pattern, group holding the board token)
_ATS_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "greenhouse",
        re.compile(
            r"(?:boards|job-boards)\.greenhouse\.io/(?:embed/job_board\?for=|embed/)?([A-Za-z0-9_.-]+)"
        ),
    ),
    ("lever", re.compile(r"jobs\.lever\.co/([A-Za-z0-9_.-]+)")),
    ("ashby", re.compile(r"jobs\.ashbyhq\.com/([A-Za-z0-9_.-]+)")),
    ("smartrecruiters", re.compile(r"(?:careers|jobs)\.smartrecruiters\.com/([A-Za-z0-9_.-]+)")),
)
_WORKABLE_PATH = re.compile(r"apply\.workable\.com/([A-Za-z0-9_.-]+)")
_WORKABLE_HOST = re.compile(r"([A-Za-z0-9-]+)\.workable\.com")

PREVIEW_LIMIT = 5
PREVIEW_TIMEOUT = 30.0


def recognise(url: str) -> tuple[str, str]:
    """Return ``(kind, token)`` for a board URL; unknown pages are ``careers``.

    Raises ValueError when the URL is not http(s), which is the only case that
    must be refused rather than degraded to a generic careers page.
    """
    parsed = urlparse(url.strip())
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise ValueError("url must start with http:// or https://")

    haystack = f"{parsed.netloc}{parsed.path}"
    for kind, pattern in _ATS_PATTERNS:
        match = pattern.search(haystack)
        if match:
            token = match.group(1).strip("/")
            # A bare board homepage ("/") is not a token.
            if token and token not in {"embed", "job_board", "jobs"}:
                return kind, token

    for pattern in (_WORKABLE_PATH, _WORKABLE_HOST):
        match = pattern.search(haystack)
        if match:
            token = match.group(1).strip("/")
            if token and token != "www":
                return "workable", token

    return CAREERS_KIND, ""


def _build_one(kind: str, token: str, label: str, url: str, client: httpx.AsyncClient | None) -> BaseSource:
    if kind == CAREERS_KIND:
        from app.sources.careers import OfficialCareersSource

        return OfficialCareersSource(
            company_name=label or urlparse(url).netloc,
            url=url,
            client=client,
        )

    cls = all_sources().get(kind)
    if cls is None:
        raise ValueError(f"unknown board kind {kind!r}")
    return cls(board_token=token, company_name=label, client=client)


def build_custom_boards(
    client: httpx.AsyncClient,
    *,
    kinds: tuple[str, ...] = BOARD_KINDS,
) -> list[BaseSource]:
    """Instantiate enabled boards of ``kinds`` from the database.

    A read failure returns no boards: polling must continue with the configured
    sources rather than aborting the whole cycle.
    """
    try:
        with session_scope() as session:
            rows = session.exec(select(CustomBoard).where(CustomBoard.enabled.is_(True))).all()
            configs = [
                (row.kind, row.token, row.label, row.url)
                for row in rows
                if row.kind in kinds
            ]
    except Exception as exc:  # noqa: BLE001 - a poll must survive this
        log.warning("custom boards unavailable: %s", exc)
        return []

    built: list[BaseSource] = []
    for kind, token, label, url in configs:
        try:
            built.append(_build_one(kind, token, label, url, client))
        except (KeyError, TypeError, ValueError) as exc:
            log.warning("cannot build custom board %s: %s", url, exc)
    return built


async def preview(url: str) -> dict:
    """Fetch a board once, without saving it, so the admin sees what it parses.

    Bounded by ``PREVIEW_TIMEOUT`` and capped to a handful of samples: this runs
    inside a request and a broken site must not hold the admin's page open.
    """
    kind, token = recognise(url)
    source = _build_one(kind, token, "", url, None)
    async with source:
        async with asyncio.timeout(PREVIEW_TIMEOUT):
            jobs: list[RawJob] = await source.fetch()

    return {
        "kind": kind,
        "token": token,
        "label": getattr(source, "company_name", "") or urlparse(url).netloc,
        "found": len(jobs),
        "samples": [
            {
                "title": job.title,
                "company": job.company,
                "location": job.location,
                "url": job.apply_url,
            }
            for job in jobs[:PREVIEW_LIMIT]
        ],
    }
