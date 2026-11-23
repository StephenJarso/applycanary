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
from datetime import date, timedelta
from typing import Any
from urllib.parse import unquote, urlparse

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
            entries.append({
                "title": title,
                "url": url,
                "description": desc,
                "published": (item.findtext("pubDate") or "").strip(),
            })

    if not entries:
        for entry in root.findall(".//atom:entry", ns)[:max_entries]:
            title = (entry.findtext("atom:title", namespaces=ns) or "").strip()
            link_el = entry.find("atom:link", ns)
            url = (link_el.get("href") if link_el is not None else "") or ""
            summary = (entry.findtext("atom:summary", namespaces=ns) or "").strip()
            if title and url:
                entries.append({
                    "title": title,
                    "url": url,
                    "description": summary,
                    "published": (entry.findtext("atom:published", namespaces=ns) or "").strip(),
                })

    return entries


def _strip_tags(html: str) -> str:
    """Very lightweight HTML tag stripper (no deps)."""
    return re.sub(r"<[^>]+>", " ", html).strip()


def _current_year() -> int:
    return date.today().year


def _host_label(url: str) -> str:
    """A readable organiser name from a URL ("kyiv.dev" → "Kyiv.dev")."""
    try:
        host = urlparse(url).netloc.lower().removeprefix("www.") or ""
    except (ValueError, TypeError):
        return ""
    return host[:1].upper() + host[1:] if host else ""


# DuckDuckGo Lite wraps every external result in a `uddg=` redirect. The old
# fallback looked for `class="result-link"`, which the endpoint no longer
# renders, so it silently returned nothing on every run.
_DDG_RESULT_RE = re.compile(
    r'<a[^>]*href="[^"]*?uddg=([^&"]+)[^"]*"[^>]*>(.*?)</a>',
    re.IGNORECASE | re.DOTALL,
)


def _parse_ddg_lite(html: str, *, limit: int) -> list[tuple[str, str]]:
    """(url, title) pairs from a DuckDuckGo lite SERP, deduplicated by URL.

    The anchor text carries the page title; `uddg=` carries the real target,
    so the destination is taken from the parameter rather than the href.
    """
    seen: set[str] = set()
    out: list[tuple[str, str]] = []
    for match in _DDG_RESULT_RE.finditer(html):
        url = unquote(match.group(1))
        if not url.startswith("http"):
            continue
        title = _strip_tags(match.group(2))
        title = re.sub(r"\s+", " ", title).strip()
        if not title or url in seen:
            continue
        seen.add(url)
        out.append((url, title))
        if len(out) >= limit:
            break
    return out


# The tech check keeps medical and sales conference crew calls out of a
# developer's volunteer tab; the title gate lives with the scraper below.
_TECH_RE = re.compile(
    r"tech|software|developer|coding|programm|python|django|javascript|typescript|"
    r"\bweb\b|cloud|\bdata\b|\bai\b|machine learning|open[- ]source|hackathon|"
    r"devops|android|flutter|wordpress|\bgdg\b|devfest|pycon|conference|summit|"
    r"cyber|security|blockchain|linux|kubernetes|docker|\bui[- /]?ux\b|design|"
    r"engineer|comput|\bict\b|geek|developers?",
    re.I,
)

# The volunteer term has to be in the headline, not merely somewhere in the
# body: a fellowship post that says "volunteer" once is not a crew call, and
# the card would be lying about what it is.
_VOLUNTEER_TITLE_RE = re.compile(
    r"volunteer|crew|call for speakers?|looking for (speakers|volunteers)|"
    r"speakers? (wanted|needed)|call for (papers|proposals)|\bcfp\b",
    re.I,
)
_MAX_AGE_DAYS = 365

_VOLUNTEER_QUERIES = (
    f"call for volunteers tech conference {_current_year()}",
    "call for volunteers developer conference Africa",
    f"DevFest volunteer {_current_year()}",
)

# Site-search feeds, not home feeds: each returns only posts matching the
# term, which is the whole feed this source exists for. WordPress accepts
# `feed=rss2` on a search URL, so no page scraping is needed.
_VOLUNTEER_FEEDS = (
    ("WordCamp Central", "https://central.wordcamp.org/?s=call+for+volunteers&feed=rss2"),
    ("Opportunities for Africans", "https://opportunitiesforafricans.com/feed/?s=volunteer"),
)


def _recently_published(published: str) -> bool:
    """False only when the feed states a date older than the cut-off."""
    from email.utils import parsedate_to_datetime

    if not published:
        return True  # a feed that omits dates must not be discarded wholesale
    try:
        posted = parsedate_to_datetime(published)
    except (TypeError, ValueError):
        return True
    return date.today() - posted.date() <= timedelta(days=_MAX_AGE_DAYS)


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


def _volunteer_entry(
    *, title: str, org: str, url: str, description: str, extra_tags: list[str] | None = None
) -> dict:
    """A scraped volunteer call in the card's shape.

    Compensation is stated as unknown rather than guessed: a conference crew
    may waive the ticket or cover board, and the listing is the only place
    that says which. The card prints this line verbatim.
    """
    tags = ["volunteer", "tech events", *(extra_tags or [])]
    return {
        "title": title.strip()[:200],
        "org": org,
        "url": url,
        "stipend": "Unpaid volunteer role (benefits not stated — check the listing)",
        "closes": None,
        "when": "Rolling — check the listing for crew dates",
        "city": "Various",
        "steps": [
            "Open the volunteer page linked above",
            "Check the crew dates, whether travel or the ticket is covered, and any language requirement",
            "Apply through the form on that page before the call closes",
        ],
        "tags": tags,
        "kind": "Volunteer",
        "region": "Global",
        "description": description[:800],
    }


def _is_tech_volunteer(title: str, body: str = "") -> bool:
    """A headline that asks for crew/speakers on a tech event.

    The title gate is what keeps unrelated posts out: an opportunity blog
    mentions "volunteer" in passing all the time, but a real crew call says
    it in the headline, and only tech events pass the second check.
    """
    return bool(
        _VOLUNTEER_TITLE_RE.search(title) and _TECH_RE.search(f"{title} {body}")
    )


def _scrape_tech_volunteers() -> list[dict]:
    """Tech-event volunteer calls from feeds and a search-engine fallback.

    Conference crews are announced on the event's own page, not on a job
    board, so nothing indexes them. WordPress site-search feeds carry the
    calls that do get published (WordCamp Central, African opportunity
    blogs); DuckDuckGo then reaches conference sites that never enter a feed.
    Only the SERP is read — no detail crawl — and the search half is skipped
    the moment the engine serves a challenge page, so a blocked search adds
    no noise and no retries.
    """
    results: list[dict] = []
    seen_urls: set[str] = set()

    for feed_name, feed_url in _VOLUNTEER_FEEDS:
        resp = _get(feed_url, allow_redirects=True)
        if resp is None:
            continue
        for entry in _parse_rss(resp.text, max_entries=20):
            if not _recently_published(entry.get("published", "")):
                continue
            description = _strip_tags(entry["description"])
            if entry["url"] in seen_urls or not _is_tech_volunteer(
                entry["title"], description
            ):
                continue
            seen_urls.add(entry["url"])
            results.append(
                _volunteer_entry(
                    title=entry["title"],
                    org=feed_name,
                    url=entry["url"],
                    description=description,
                    extra_tags=["from feed"],
                )
            )

    for query in _VOLUNTEER_QUERIES:
        resp = _get("https://lite.duckduckgo.com/lite/", params={"q": query})
        if resp is None:
            continue
        if resp.status_code != 200 or "uddg=" not in resp.text:
            # A 202 is DuckDuckGo's bot challenge; every further query would
            # hit the same wall, so stop rather than retry the whole list.
            log.info("scraper: volunteer search blocked (%s); using feeds only", resp.status_code)
            break
        for url, title in _parse_ddg_lite(resp.text, limit=8):
            if url in seen_urls or not _is_tech_volunteer(title, url):
                continue
            seen_urls.add(url)
            results.append(
                _volunteer_entry(
                    title=title,
                    org=_host_label(url) or "Event organiser",
                    url=url,
                    description=(
                        f"Volunteer call discovered by searching for \"{query}\". "
                        "Open the page for the crew dates and what the organiser covers."
                    ),
                    extra_tags=["from web search"],
                )
            )

    return results[:25]


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
        for url, title in _parse_ddg_lite(resp.text, limit=10):
            if url in seen_urls:
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
                "org": _host_label(url) or "Various",
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
        ("Web search", _scrape_tech_volunteers),
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
                # Cards render when/city/steps, and a scraped volunteer call
                # without them shows an empty meta line and no timeline.
                raw_steps = entry.get("steps") or []
                if isinstance(raw_steps, str):
                    raw_steps = [raw_steps]
                row = ProgrammeEntry(
                    kind=kind,
                    title=title,
                    org=str(entry.get("org") or ""),
                    when=str(entry.get("when") or ""),
                    closes=entry.get("closes"),
                    date=entry.get("date"),
                    steps=[str(step) for step in raw_steps if str(step).strip()],
                    city=str(entry.get("city") or ""),
                    tags=list(tags),
                    stipend=entry.get("stipend"),
                    source=source_name,
                    url=str(entry.get("url") or ""),
                    desc=str(entry.get("description") or "")[:2000],
                    remote_friendly=entry.get("remote_friendly", True),
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
