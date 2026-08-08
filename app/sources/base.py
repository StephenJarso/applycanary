"""Source connector contract and registry.

A connector's only job is to turn one board's payload into `RawJob` objects. HTTP
retries, timeouts, error isolation and telemetry live here so adding a board is a
thin parser, not a fresh pile of plumbing.
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import UTC, datetime

import httpx

from app.models import Job, utcnow
from app.pipeline.normalize import (
    canonical_url,
    fingerprint,
    is_remote,
    text_hash,
)

log = logging.getLogger(__name__)

DEFAULT_TIMEOUT = 20.0
# Identify the client honestly and give operators a way to reach the user.
USER_AGENT = "applycanary/0.1 (personal job search agent; +https://github.com)"


@dataclass(slots=True)
class RawJob:
    """Source-agnostic posting, before dedup and persistence."""

    source: str
    source_id: str
    company: str
    title: str
    location: str = ""
    description: str = ""
    apply_url: str = ""
    posted_at: datetime | None = None
    salary_min: int | None = None
    salary_max: int | None = None
    salary_currency: str = ""
    salary_is_estimate: bool = False
    remote_flag: bool | None = None
    ats_platform: str = ""
    ats_board_token: str = ""
    extra: dict = field(default_factory=dict)

    def to_job(self) -> Job:
        remote = is_remote(self.location, description=self.description, flag=self.remote_flag)
        return Job(
            fingerprint=fingerprint(self.company, self.title, self.location, remote=remote),
            source=self.source,
            source_id=str(self.source_id),
            company=self.company.strip(),
            title=self.title.strip(),
            location=self.location.strip(),
            is_remote=remote,
            description=self.description,
            description_hash=text_hash(self.description),
            salary_min=self.salary_min,
            salary_max=self.salary_max,
            salary_currency=self.salary_currency,
            salary_is_estimate=self.salary_is_estimate,
            apply_url=self.apply_url,
            canonical_url=canonical_url(self.apply_url),
            ats_platform=self.ats_platform,
            ats_board_token=self.ats_board_token,
            posted_at=self.posted_at,
            first_seen_at=utcnow(),
            last_seen_at=utcnow(),
        )


class BaseSource(ABC):
    """Subclasses implement `fetch`. `name` must be unique in the registry."""

    name: str = ""
    # False when required credentials are absent; ingest skips it quietly.
    enabled: bool = True
    # True for connectors that parse HTML and will break on markup changes.
    is_scraper: bool = False

    def __init__(self, client: httpx.AsyncClient | None = None) -> None:
        self._client = client
        self._owns_client = client is None

    async def __aenter__(self) -> BaseSource:
        if self._client is None:
            self._client = httpx.AsyncClient(
                timeout=DEFAULT_TIMEOUT,
                headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
                follow_redirects=True,
            )
        return self

    async def __aexit__(self, *_exc: object) -> None:
        if self._owns_client and self._client is not None:
            await self._client.aclose()
            self._client = None

    @property
    def client(self) -> httpx.AsyncClient:
        if self._client is None:
            raise RuntimeError(f"{self.name}: use `async with` before fetching")
        return self._client

    @abstractmethod
    async def fetch(self) -> list[RawJob]:
        """Return current postings. Raise on hard failure; ingest isolates it."""

    # ------------------------------------------------------------------
    async def get_json(self, url: str, **kwargs: object) -> object:
        """GET with one retry on transient failure.

        Retries only 429/5xx and network errors. A 404 means a wrong board token,
        which retrying will never fix.
        """
        last: Exception | None = None
        retryable = (429, 500, 502, 503, 504)
        for attempt in (1, 2):
            try:
                resp = await self.client.get(url, **kwargs)  # type: ignore[arg-type]
                resp.raise_for_status()
                return resp.json()
            except (httpx.HTTPStatusError, httpx.RequestError) as exc:
                status = getattr(getattr(exc, "response", None), "status_code", None)
                if status is not None and status not in retryable:
                    raise
                last = exc
                if attempt == 2:
                    break
                log.debug("%s: retrying %s after %s", self.name, url, exc)
        assert last is not None
        raise last


# ---------------------------------------------------------------- registry

_REGISTRY: dict[str, type[BaseSource]] = {}


def register(cls: type[BaseSource]) -> type[BaseSource]:
    """Class decorator. Adding a board is one file plus this line."""
    if not cls.name:
        raise ValueError(f"{cls.__name__} must define a name")
    if cls.name in _REGISTRY:
        raise ValueError(f"duplicate source name {cls.name!r}")
    _REGISTRY[cls.name] = cls
    return cls


def get_source(name: str) -> type[BaseSource]:
    return _REGISTRY[name]


def all_sources() -> dict[str, type[BaseSource]]:
    return dict(_REGISTRY)


def parse_epoch(value: object, *, unit: str = "s") -> datetime | None:
    """Epoch timestamp to naive UTC, matching the convention in `app/models.py`.

    Boards disagree on units: Lever sends milliseconds, Arbeitnow and Himalayas
    send seconds. Pass `unit="ms"` for the former.

    Values are sanity-bounded to 2000-2100 because a board occasionally emits 0
    or a placeholder, and a job dated 1970 would sort to the bottom forever
    while one dated 2255 would pin to the top.
    """
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    seconds = float(value) / 1000.0 if unit == "ms" else float(value)
    if not 946_684_800 <= seconds <= 4_102_444_800:
        return None
    try:
        return datetime.fromtimestamp(seconds, tz=UTC).replace(tzinfo=None)
    except (OverflowError, OSError, ValueError):
        return None


def clean_html(raw: str) -> str:
    """Flatten job-description HTML to readable text.

    Uses the stdlib parser rather than adding a dependency; descriptions only
    need to be searchable and human-readable, not structurally faithful.
    """
    if not raw:
        return ""
    from html import unescape
    from html.parser import HTMLParser

    class _Strip(HTMLParser):
        def __init__(self) -> None:
            super().__init__(convert_charrefs=True)
            self.parts: list[str] = []
            self._skip = 0

        def handle_starttag(self, tag: str, attrs: object) -> None:  # noqa: ARG002
            if tag in ("script", "style"):
                self._skip += 1
            elif tag in ("p", "br", "div", "li", "tr", "h1", "h2", "h3", "h4"):
                self.parts.append("\n")
            if tag == "li":
                self.parts.append("- ")

        def handle_endtag(self, tag: str) -> None:
            if tag in ("script", "style") and self._skip:
                self._skip -= 1

        def handle_data(self, data: str) -> None:
            if not self._skip:
                self.parts.append(data)

    stripper = _Strip()
    try:
        stripper.feed(unescape(raw))
    except Exception:  # noqa: BLE001 - malformed markup should not kill a poll
        return raw
    text = "".join(stripper.parts)
    lines = [ln.strip() for ln in text.splitlines()]
    return "\n".join(ln for ln in lines if ln).strip()
