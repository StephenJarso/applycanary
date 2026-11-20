"""Generic RSS 2.0 / Atom feed connector for opportunity sources.

Used by opportunity feeds that publish a standard feed (opportunitiesforafricans.com,
africatechjobs.com, etc.). Each feed entry is returned as a minimal dict rather than
a RawJob — opportunity feeds carry different fields than job boards.
"""
from __future__ import annotations

import logging
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import datetime

import httpx

log = logging.getLogger(__name__)

USER_AGENT = "applycanary/0.1 (personal opportunity tracker)"
REQUEST_TIMEOUT = 20.0


@dataclass
class FeedEntry:
    title: str
    url: str
    description: str
    published: datetime | None
    tags: list[str] = field(default_factory=list)
    org: str = ""


async def fetch_rss(url: str, *, max_entries: int = 20) -> list[FeedEntry]:
    """Fetch and parse an RSS 2.0 or Atom feed. Never raises — returns [] on failure."""
    try:
        async with httpx.AsyncClient(
            headers={"User-Agent": USER_AGENT},
            timeout=REQUEST_TIMEOUT,
            follow_redirects=True,
        ) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            return _parse(resp.text, max_entries=max_entries)
    except Exception as exc:  # noqa: BLE001
        log.warning("rss: failed to fetch %s: %s", url, exc)
        return []


def _parse(xml_text: str, *, max_entries: int) -> list[FeedEntry]:
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as exc:
        log.warning("rss: XML parse error: %s", exc)
        return []

    ns = {"atom": "http://www.w3.org/2005/Atom"}
    entries: list[FeedEntry] = []

    # RSS 2.0
    for item in root.findall(".//item")[:max_entries]:
        title = (item.findtext("title") or "").strip()
        url = (item.findtext("link") or "").strip()
        desc = (item.findtext("description") or "").strip()
        pub_str = (item.findtext("pubDate") or "").strip()
        cats = [c.text or "" for c in item.findall("category") if c.text]
        if title and url:
            entries.append(FeedEntry(
                title=title, url=url, description=desc,
                published=_parse_date(pub_str), tags=cats,
            ))

    # Atom
    if not entries:
        for entry in root.findall(".//atom:entry", ns)[:max_entries]:
            title = (entry.findtext("atom:title", namespaces=ns) or "").strip()
            link_el = entry.find("atom:link", ns)
            url = (link_el.get("href") if link_el is not None else "") or ""
            summary = (entry.findtext("atom:summary", namespaces=ns) or "").strip()
            pub_str = (entry.findtext("atom:published", namespaces=ns) or "").strip()
            if title and url:
                entries.append(FeedEntry(
                    title=title, url=url, description=summary,
                    published=_parse_date(pub_str),
                ))

    return entries


def _parse_date(s: str) -> datetime | None:
    from email.utils import parsedate_to_datetime
    if not s:
        return None
    try:
        return parsedate_to_datetime(s).replace(tzinfo=None)
    except Exception:  # noqa: BLE001
        pass
    for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(s[:19], fmt)
        except ValueError:
            continue
    return None
