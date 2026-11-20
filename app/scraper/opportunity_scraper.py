"""Live opportunity scraper: fetches from public sources and upserts into ProgrammeEntry.

Runs daily via the scheduler (job_scrape_programmes). Each source is independently
fault-tolerant — a network error on one never aborts the others.

Sources:
- Outreachy blog RSS (internships)
- Google Summer of Code HTML (internships)
- MLH Fellowship HTML (internships)
- Idealist.org RSS volunteer feed
- HOT (Humanitarian OpenStreetMap Team) jobs page
- DuckDuckGo Lite fallback search results

All HTTP calls use requests with a polite User-Agent and explicit timeouts.
RSS feeds are parsed with xml.etree.ElementTree (stdlib).
"""
from __future__ import annotations

import logging
import re
import xml.etree.ElementTree as ET
from datetime import date
from typing import Any

import requests
from sqlmodel import Session, select

from app.models import ProgrammeEntry

log = logging.getLogger(__name__)

USER_AGENT = "applycanary/0.1 (personal opportunity tracker; +https://github.com)"
REQUEST_TIMEOUT = 20

# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

_SESSION = requests.Session()
_SESSION.headers.update({"User-Agent": USER_AGENT})


def _get(url: str, **kwargs: Any) -> requests.Response | None:
    """GET with timeout and polite User-Agent. Returns None on any error."""
    try:
        resp = _SESSION.get(url, timeout=REQUEST_TIMEOUT, **kwargs)
        resp.raise_for_status()
        return resp
    except Exception as exc:  # noqa: BLE001
        log.warning("scraper: GET %s failed: %s", url, exc)
        return None


def _parse_rss(xml_text: str, *, max_entries: int = 20) -> list[dict]:
    """Parse RSS 2.0 / Atom feed into list of raw dicts."""
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as exc:
        log.warning("scraper: RSS XML parse error: %s", exc)
        return []

    ns = {"atom": "http://www.w3.org/2005/Atom"}
    entries: list[dict] = []

    for item in root.findall(".//item")[:max_entries]:
        title = (item.findtext("title") or "").strip()
        url = (item.findtext("link") or "").strip()
        desc = (item.findtext("description") or "").strip()
        if title and url:
            entries.append({"title": title, "url": url, "description": desc})

    if not entries:
        for entry in root.findall(".//atom:entry", ns)[:max_entries]:
            title = (entry.findtext("atom:title", namespaces=ns) or "").strip()
            link_el = entry.find("atom:link", ns)
            url = (link_el.get("href") if link_el is not None else "") or ""
            summary = (entry.findtext("atom:summary", namespaces=ns) or "").strip()
            if title and url:
                entries.append({"title": title, "url": url, "description": summary})

    return entries


def _strip_tags(html: str) -> str:
    """Very lightweight HTML tag stripper (no deps)."""
    return re.sub(r"<[^>]+>", " ", html).strip()


def _current_year() -> int:
    return date.today().year


# ---------------------------------------------------------------------------
# Individual source scrapers — each returns list[dict] with keys:
#   title, org, url, stipend, closes, tags, kind, region, description
# ---------------------------------------------------------------------------

def _scrape_outreachy() -> list[dict]:
    """Outreachy internship rounds via blog RSS feed."""
    resp = _get("https://www.outreachy.org/blog/feed/")
    if resp is None:
        return []
    raw = _parse_rss(resp.text, max_entries=10)
    results = []
    for entry in raw:
        title_low = entry["title"].lower()
        if not any(kw in title_low for kw in ("intern", "round", "cohort", "apply", "open")):
            continue
        results.append({
            "title": entry["title"][:200],
            "org": "Outreachy",
            "url": entry["url"],
            "stipend": "Paid: $7,000 USD stipend for 3 months",
            "closes": None,
            "tags": ["open-source", "internship", "remote", "diversity"],
            "kind": "Internship",
            "region": "Global",
            "description": _strip_tags(entry["description"])[:800],
        })
    return results


def _scrape_gsoc() -> list[dict]:
    """Google Summer of Code — static well-known entry updated when scraping detects open program."""
    resp = _get("https://summerofcode.withgoogle.com/", allow_redirects=True)
    if resp is None:
        return []
    html = resp.text
    year = _current_year()
    # Look for signals that applications are currently open
    open_signals = ["apply now", "applications open", "start applying", "submit your proposal"]
    is_open = any(sig in html.lower() for sig in open_signals)
    if not is_open:
        return []
    return [{
        "title": f"Google Summer of Code {year}",
        "org": "Google",
        "url": "https://summerofcode.withgoogle.com/",
        "stipend": "Paid: $1,500–$6,600 USD depending on project size and country",
        "closes": None,
        "tags": ["open-source", "internship", "remote", "gsoc", "google"],
        "kind": "Internship",
        "region": "Global",
        "description": (
            f"Google Summer of Code {year} is open for contributor applications. "
            "Work with open source organisations over 12 weeks, mentored by experienced developers."
        ),
    }]


def _scrape_mlh_fellowship() -> list[dict]:
    """MLH Fellowship — detects open batches from landing page."""
    resp = _get("https://fellowship.mlh.io/", allow_redirects=True)
    if resp is None:
        return []
    html = resp.text.lower()
    open_signals = ["apply now", "applications open", "now accepting", "apply today"]
    is_open = any(sig in html for sig in open_signals)
    if not is_open:
        return []
    year = _current_year()
    return [{
        "title": f"MLH Fellowship {year}",
        "org": "Major League Hacking (MLH)",
        "url": "https://fellowship.mlh.io/",
        "stipend": "Paid: $5,000 USD stipend for 12-week batch",
        "closes": None,
        "tags": ["open-source", "internship", "remote", "mlh", "software-engineering"],
        "kind": "Internship",
        "region": "Global",
        "description": (
            "The MLH Fellowship is a 12-week remote internship alternative where "
            "you contribute to open source projects while earning a stipend."
        ),
    }]


def _scrape_idealist_volunteers() -> list[dict]:
    """Idealist.org volunteer RSS feed."""
    # Try a few region variants — Idealist's feed URLs vary
    feed_urls = [
        "https://www.idealist.org/en/jobs/feed?type=VOLUNTEER&country=KE",
        "https://www.idealist.org/en/jobs/feed?type=VOLUNTEER",
    ]
    for url in feed_urls:
        resp = _get(url)
        if resp is None:
            continue
        raw = _parse_rss(resp.text, max_entries=15)
        if not raw:
            continue
        results = []
        for entry in raw:
            results.append({
                "title": entry["title"][:200],
                "org": "Idealist.org",
                "url": entry["url"],
                "stipend": "Unpaid volunteer role",
                "closes": None,
                "tags": ["volunteer", "ngo", "remote"],
                "kind": "Volunteer",
                "region": "Global",
                "description": _strip_tags(entry["description"])[:800],
            })
        return results
    return []


def _scrape_hotosm() -> list[dict]:
    """HOT (Humanitarian OpenStreetMap Team) jobs page — scrape listings."""
    resp = _get("https://www.hotosm.org/jobs/")
    if resp is None:
        return []
    html = resp.text
    # Find job links: HOT job pages typically have links ending in /jobs/<slug>
    links = re.findall(
        r'href="(https?://www\.hotosm\.org/jobs/[^"]+)"',
        html,
    )
    # Deduplicate and exclude the index page itself
    seen: set[str] = set()
    results = []
    for link in links:
        if link in seen or link.rstrip("/") == "https://www.hotosm.org/jobs":
            continue
        seen.add(link)
        # Extract a rough title from the URL slug
        slug = link.rstrip("/").rsplit("/", 1)[-1]
        title = slug.replace("-", " ").title()
        if not title:
            continue
        # Try to decide kind: volunteer vs paid
        slug_low = slug.lower()
        is_volunteer = any(kw in slug_low for kw in ("volunteer", "community", "mapper"))
        kind = "Volunteer" if is_volunteer else "Internship"
        stipend = "Unpaid volunteer role" if is_volunteer else "Paid: see listing"
        results.append({
            "title": f"HOT – {title}"[:200],
            "org": "Humanitarian OpenStreetMap Team (HOT)",
            "url": link,
            "stipend": stipend,
            "closes": None,
            "tags": ["openstreetmap", "humanitarian", "gis", "remote"],
            "kind": kind,
            "region": "Global",
            "description": "Role at the Humanitarian OpenStreetMap Team. See listing for details.",
        })
    return results[:10]


def _scrape_ddg_fallback() -> list[dict]:
    """DuckDuckGo Lite search fallback for rare opportunities."""
    DDG_LITE = "https://lite.duckduckgo.com/lite/"
    year = _current_year()
    queries = [
        f"open source fellowship developer Africa {year}",
        f"tech volunteership remote Africa {year}",
        f"developer internship fellowship East Africa {year}",
    ]
    results: list[dict] = []
    seen_urls: set[str] = set()
    for query in queries:
        resp = _get(DDG_LITE, params={"q": query})
        if resp is None:
            continue
        html = resp.text
        # DuckDuckGo Lite result links are inside <a class="result-link">
        for m in re.finditer(
            r'<a[^>]+class="result-link"[^>]*href="([^"]+)"[^>]*>([^<]+)</a>',
            html,
        ):
            url, title = m.group(1), m.group(2).strip()
            # Redirect URLs from DDG Lite look like //duckduckgo.com/l/?uddg=...
            if url.startswith("//"):
                url = "https:" + url
            # Extract actual destination from uddg= param
            uddg = re.search(r"uddg=([^&]+)", url)
            if uddg:
                from urllib.parse import unquote
                url = unquote(uddg.group(1))
            if url in seen_urls or not url.startswith("http"):
                continue
            seen_urls.add(url)
            # Classify by keywords
            title_low = title.lower()
            if any(kw in title_low for kw in ("volunteer", "volunteership")):
                kind, stipend = "Volunteer", "Unpaid volunteer role"
                tags = ["volunteer", "remote"]
            else:
                kind, stipend = "Internship", "Paid: see listing"
                tags = ["fellowship", "internship", "remote"]
            results.append({
                "title": title[:200],
                "org": "Various",
                "url": url,
                "stipend": stipend,
                "closes": None,
                "tags": tags,
                "kind": kind,
                "region": "Global",
                "description": f"Discovered via DuckDuckGo search: {query}",
            })
            if len(results) >= 10:
                break
        if len(results) >= 10:
            break
    return results


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def scrape_and_upsert_opportunities(session: Session) -> int:
    """Scrape live sources and upsert new opportunities. Returns count added."""
    # Load existing (kind, title) pairs to avoid duplicate inserts
    try:
        existing: set[tuple[str, str]] = {
            (row.kind, row.title)
            for row in session.exec(select(ProgrammeEntry)).all()
        }
    except Exception:  # noqa: BLE001
        log.exception("scraper: failed to load existing programmes")
        return 0

    scrapers = [
        ("outreachy", _scrape_outreachy),
        ("gsoc", _scrape_gsoc),
        ("mlh", _scrape_mlh_fellowship),
        ("idealist", _scrape_idealist_volunteers),
        ("hotosm", _scrape_hotosm),
        ("ddg_fallback", _scrape_ddg_fallback),
    ]

    added = 0
    for source_name, scraper_fn in scrapers:
        try:
            entries = scraper_fn()
        except Exception:  # noqa: BLE001
            log.exception("scraper: source %r raised unexpectedly", source_name)
            continue

        for entry in entries:
            kind = str(entry.get("kind") or "Internship")
            title = str(entry.get("title") or "").strip()
            if not title:
                continue
            key = (kind, title)
            if key in existing:
                continue
            existing.add(key)

            # Map incoming dict to ProgrammeEntry columns
            tags = entry.get("tags") or []
            if isinstance(tags, str):
                tags = [tags]

            try:
                row = ProgrammeEntry(
                    kind=kind,
                    title=title,
                    org=str(entry.get("org") or ""),
                    closes=entry.get("closes"),
                    tags=list(tags),
                    stipend=entry.get("stipend"),
                    source=source_name,
                    url=str(entry.get("url") or ""),
                    desc=str(entry.get("description") or "")[:2000],
                    remote_friendly=True,
                )
                session.add(row)
                added += 1
                log.debug("scraper: added %r (%s) from %s", title, kind, source_name)
            except Exception:  # noqa: BLE001
                log.exception("scraper: failed to build row for %r", title)

    if added:
        try:
            session.commit()
        except Exception:  # noqa: BLE001
            log.exception("scraper: commit failed after adding %d entries", added)
            session.rollback()
            return 0

    log.info("scraper: added %d new programme entries", added)
    return added
