"""Official company career-site connector.

Most postings first appear on a company's own careers site and only later reach
an aggregator — if they reach one at all. This connector reads that site
directly, so niche roles (regional internships, small teams, unusual stacks)
show up while they are still unseen by the big boards.

Two entry points are supported, configured in ``companies.yaml`` under
``careers:``:

* ``url``      — one careers page.
* ``sitemap``  — a sitemap (or sitemap index) whose URLs are filtered with
  ``include`` / ``exclude`` substrings. This is how a WordPress careers site
  with one page per role is polled without crawling its navigation.

Pages are parsed in this order: schema.org/JobPosting JSON-LD when present,
then a conservative HTML fallback (Open Graph/title, meta description, main
content, first application link). Sites change markup; per-page errors are
isolated so one broken page never fails the source, and the run is recorded
through SourceRun like every other connector.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from datetime import datetime
from html import unescape
from typing import Any
from urllib.parse import urljoin, urlparse

from app.sources.base import BaseSource, RawJob, clean_html, register
from app.sources.greenhouse import parse_iso

log = logging.getLogger(__name__)

MAX_PAGES = 60
MAX_CONCURRENT_PAGES = 5
PAGE_TIMEOUT = 25.0
MAX_DESCRIPTION_CHARS = 20000

_LOC_RE = re.compile(r"<loc>\s*([^<\s]+)\s*</loc>", re.IGNORECASE)
_TITLE_RE = re.compile(r"<title[^>]*>(.*?)</title>", re.IGNORECASE | re.DOTALL)
_META_RE = re.compile(r"<meta\b[^>]*>", re.IGNORECASE)
_ATTR_RE = re.compile(r'([\w:.-]+)\s*=\s*["\']([^"\']*)["\']')
_MAIN_RE = re.compile(r"<main\b[^>]*>(.*?)</main>", re.IGNORECASE | re.DOTALL)
_BODY_RE = re.compile(r"<body\b[^>]*>(.*?)</body>", re.IGNORECASE | re.DOTALL)
_SCRIPT_RE = re.compile(
    r'<script[^>]*type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
    re.IGNORECASE | re.DOTALL,
)
_LINK_RE = re.compile(r"<a\b[^>]*>.*?</a>", re.IGNORECASE | re.DOTALL)
_HREF_RE = re.compile(r'href\s*=\s*["\']([^"\']+)["\']', re.IGNORECASE)
_GENERIC_TITLES = {"apply", "apply now", "careers", "jobs", "job openings"}


@register
class OfficialCareersSource(BaseSource):
    name = "careers"
    is_scraper = True

    def __init__(
        self,
        company_name: str = "",
        url: str = "",
        sitemap: str = "",
        include: list[str] | None = None,
        exclude: list[str] | None = None,
        exclude_exact: list[str] | None = None,
        location: str = "",
        max_pages: int = MAX_PAGES,
        **kw: Any,
    ) -> None:
        super().__init__(**kw)
        self.company_name = company_name.strip()
        self.url = url.strip()
        self.sitemap = sitemap.strip()
        self.include = [p for p in (include or []) if p]
        self.exclude = [p for p in (exclude or []) if p]
        self.exclude_exact = {u.strip().rstrip("/") for u in (exclude_exact or []) if u.strip()}
        self.location = location.strip()
        self.max_pages = max(1, min(int(max_pages), MAX_PAGES))
        # Reuse the ingest label format ("careers/hennge") for telemetry.
        self.board_token = re.sub(r"[^a-z0-9]+", "-", self.company_name.lower()).strip("-")

    async def fetch(self) -> list[RawJob]:
        urls = await self._page_urls()
        if not urls:
            return []

        sem = asyncio.Semaphore(MAX_CONCURRENT_PAGES)

        async def one(url: str) -> RawJob | None:
            async with sem:
                try:
                    resp = await self.client.get(
                        url, headers={"Accept": "text/html"}, timeout=PAGE_TIMEOUT
                    )
                    resp.raise_for_status()
                except Exception as exc:  # noqa: BLE001 - one dead page is not a failed source
                    log.warning("careers/%s: page %s failed: %s", self.board_token, url, exc)
                    return None
                return self._parse(resp.text, url)

        jobs = await asyncio.gather(*(one(u) for u in urls))
        return [j for j in jobs if j is not None]

    async def _page_urls(self) -> list[str]:
        if self.sitemap:
            xml = await self._get_sitemap(self.sitemap)
            if xml is None:
                return []
            urls = _sitemap_urls(xml)
            # A sitemap index points at child sitemaps rather than pages. Follow
            # a handful of them; a careers site rarely needs more than a few.
            for child in _select_sitemap_children(xml)[:5]:
                child_xml = await self._get_sitemap(child)
                if child_xml is not None:
                    urls.extend(_sitemap_urls(child_xml))
            return self._select_urls(urls)

        if self.url:
            return [self.url]
        return []

    async def _get_sitemap(self, url: str) -> str | None:
        try:
            resp = await self.client.get(
                url, headers={"Accept": "application/xml,text/xml"}, timeout=PAGE_TIMEOUT
            )
            resp.raise_for_status()
            return resp.text
        except Exception as exc:  # noqa: BLE001
            log.warning("careers/%s: sitemap %s failed: %s", self.board_token, url, exc)
            return None

    def _select_urls(self, urls: list[str]) -> list[str]:
        selected: list[str] = []
        seen: set[str] = set()
        for url in urls:
            if url in seen or not url.startswith(("http://", "https://")):
                continue
            if url.rstrip("/") in self.exclude_exact:
                continue
            if self.include and not any(part in url for part in self.include):
                continue
            if any(part in url for part in self.exclude):
                continue
            seen.add(url)
            selected.append(url)
            if len(selected) >= self.max_pages:
                break
        return selected

    def _parse(self, html: str, url: str) -> RawJob | None:
        posting = _jobposting(html)
        if posting:
            title = _str(posting.get("title")).strip()
            hiring = posting.get("hiringOrganization")
            company = _str(hiring.get("name")) if isinstance(hiring, dict) else _str(hiring)
            return RawJob(
                source=self.name,
                source_id=_str(posting.get("id") or url),
                company=company or self.company_name,
                title=title,
                location=_location(posting.get("jobLocation")) or self.location,
                description=clean_html(_str(posting.get("description")))[:MAX_DESCRIPTION_CHARS],
                apply_url=_apply_url(html, url),
                posted_at=parse_iso(posting.get("datePosted")),
                extra={"official": True},
            )

        title = _html_title(html)
        if not title or title.strip().lower() in _GENERIC_TITLES:
            return None

        body = _MAIN_RE.search(html) or _BODY_RE.search(html)
        description = clean_html(body.group(1) if body else html)[:MAX_DESCRIPTION_CHARS]
        meta = _meta_description(html)

        return RawJob(
            source=self.name,
            source_id=url,
            company=self.company_name,
            title=title,
            location=self.location,
            description=("\n".join(x for x in (meta, description) if x)).strip()[:MAX_DESCRIPTION_CHARS],
            apply_url=_apply_url(html, url),
            posted_at=_published_at(html),
            extra={"official": True},
        )


# ---------------------------------------------------------------- pure helpers


def _sitemap_urls(xml: str) -> list[str]:
    """URLs from a sitemap or sitemap index (children are not fetched here)."""
    return [u.strip() for u in _LOC_RE.findall(xml) if u.strip()]


def _select_sitemap_children(xml: str) -> list[str]:
    """Child sitemap URLs when ``xml`` is a sitemap index."""
    if "<sitemapindex" not in xml.lower():
        return []
    return _sitemap_urls(xml)


def _str(value: object) -> str:
    return value if isinstance(value, str) else ("" if value is None else str(value))


def _jobposting(html: str) -> dict | None:
    for raw in _SCRIPT_RE.findall(html):
        try:
            data = json.loads(raw)
        except (TypeError, ValueError):
            continue
        candidates = data.get("@graph") if isinstance(data, dict) and isinstance(data.get("@graph"), list) else [data]
        for node in candidates:
            if isinstance(node, dict) and node.get("@type") == "JobPosting":
                return node
    return None


def _meta_description(html: str) -> str:
    for tag in _META_RE.findall(html):
        attrs = {k.lower(): v for k, v in _ATTR_RE.findall(tag)}
        if attrs.get("name", "").lower() == "description" and attrs.get("content"):
            return clean_html(attrs["content"])
        if attrs.get("property", "").lower() == "og:description" and attrs.get("content"):
            return clean_html(attrs["content"])
    return ""


def _html_title(html: str) -> str:
    for tag in _META_RE.findall(html):
        attrs = {k.lower(): v for k, v in _ATTR_RE.findall(tag)}
        if attrs.get("property", "").lower() == "og:title" and attrs.get("content"):
            return _clean_title(attrs["content"])
    match = _TITLE_RE.search(html)
    return _clean_title(clean_html(match.group(1))) if match else ""


def _clean_title(value: str) -> str:
    """Drop the site-name suffix every CMS appends: "Role | Company Careers"."""
    title = re.split(r"\s+\|\s+", clean_html(value), maxsplit=1)[0].strip()
    return re.sub(r"\s*[-–—]\s*MID-CAREER\s*$", "", title, flags=re.IGNORECASE).strip()


def _apply_url(html: str, page_url: str) -> str:
    best = ("", -1)
    for anchor in _LINK_RE.findall(html):
        href_match = _HREF_RE.search(anchor)
        if not href_match:
            continue
        href = unescape(href_match.group(1)).strip()
        if not href or href.startswith(("#", "mailto:", "tel:", "javascript:")):
            continue
        url = urljoin(page_url, href)
        if urlparse(url).scheme not in ("http", "https"):
            continue
        label = f"{href} {clean_html(anchor)}".lower()
        score = 0
        if "gh_jid" in label:
            score += 5
        if any(k in label for k in ("apply", "entry", "challenge", "application")):
            score += 3
        if any(k in label for k in ("careers", "jobs", "positions")):
            score += 1
        if urlparse(url).netloc != urlparse(page_url).netloc:
            score += 1
        if score > best[1]:
            best = (url, score)
    return best[0] or page_url


def _published_at(html: str) -> datetime | None:
    for tag in _META_RE.findall(html):
        attrs = {k.lower(): v for k, v in _ATTR_RE.findall(tag)}
        if attrs.get("property", "").lower() in ("article:published_time", "og:updated_time"):
            parsed = parse_iso(attrs.get("content"))
            if parsed:
                return parsed
    return None


def _location(node: object) -> str:
    if isinstance(node, str):
        return node
    if isinstance(node, dict):
        address = node.get("address")
        if isinstance(address, dict):
            return ", ".join(
                str(v) for v in (
                    address.get("addressLocality"),
                    address.get("addressRegion"),
                    address.get("addressCountry"),
                ) if v
            )
        if node.get("name"):
            return str(node["name"])
    if isinstance(node, list):
        for item in node:
            value = _location(item)
            if value:
                return value
    return ""
