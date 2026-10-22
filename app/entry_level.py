"""work.ke entry-level technology roles.

work.ke curates Kenyan vacancies and, unlike most aggregators, links every
listing to the employer's own advert instead of collecting applications. Its
"entry-level technology" and "junior tech" pages are small and hand-checked,
which makes them a much better fit for a first IT role than a keyword search on
a general board.

The listing pages are server-rendered HTML: each role is an ``a.job-tile`` and
the detail page carries the seniority, work mode and the official apply link.
Parsing is deliberately shallow — a handful of class names, not the whole
markup — so a cosmetic redesign degrades individual fields instead of emptying
the feed. If every page fails, the last good feed is served rather than an
error.

``fetch_entry_level`` always goes to the network (the ingestion connector uses
it); ``get_entry_level`` is the cached view behind the dashboard tab.
"""

from __future__ import annotations

import asyncio
import html
import logging
import re
import time
from datetime import UTC, datetime, timedelta, timezone
from typing import Any

import httpx

log = logging.getLogger(__name__)

BASE_URL = "https://work.ke"
JOB_INDEX_PATH = "/job-index.json"
LISTING_PATHS = (
    "/jobs/entry-level-technology",
    "/jobs/junior-tech",
)
CACHE_TTL_SECONDS = 30 * 60.0
USER_AGENT = "applycanary/0.1 (personal job search agent; +https://github.com)"
MAX_DETAIL_FETCHES = 6

# The tab is specifically about entry-level roles. work.ke also has a separate
# internship collection, so internships stay out of this feed.
_ENTRY_LEVEL = {"entry level", "graduate"}
_SECTION_HEADS = {
    "what you will do",
    "requirements",
    "skills",
    "benefits",
    "about you",
}

_TILE_RE = re.compile(r'<a class="job-tile" href="([^"]+)">(.*?)</a>', re.S)
_ANCHOR_RE = re.compile(r'<a\b[^>]*href="([^"]+)"[^>]*>(.*?)</a>', re.S)
_CLOSING_RE = re.compile(
    r"Closes\s+(?:[A-Za-z]+,\s+)?(\d{1,2})\s+([A-Za-z]+)\s+(\d{4}),\s+"
    r"(\d{1,2}):(\d{2})\s+EAT"
)

_MONTHS = {
    "january": 1, "february": 2, "march": 3, "april": 4, "may": 5, "june": 6,
    "july": 7, "august": 8, "september": 9, "october": 10, "november": 11,
    "december": 12,
}

_cache: dict[str, Any] | None = None
_cached_at = 0.0
_lock = asyncio.Lock()


# ---------------------------------------------------------------- parsing


def _inline(fragment: str) -> str:
    """Visible text of a small HTML fragment, on one line."""
    text = re.sub(r"<[^>]+>", " ", fragment or "")
    text = html.unescape(text).replace("\xa0", " ")
    return re.sub(r"\s+", " ", text).strip()


def _lines(page: str) -> list[str]:
    """Visible text of a page as stripped lines, scripts and styles removed."""
    without_code = re.sub(
        r"<(script|style)\b.*?</\1>", " ", page or "", flags=re.S | re.I
    )
    text = re.sub(r"<[^>]+>", "\n", without_code)
    text = html.unescape(text).replace("\xa0", " ")
    return [line.strip() for line in text.splitlines() if line.strip()]


def parse_closing(label: str) -> datetime | None:
    """Parse work.ke's "Closes Thursday, 29 October 2026, 23:59 EAT".

    Returns an aware UTC datetime. Labels such as "Deadline not published by
    employer" mean the employer published no date — not that the role is open
    forever — so they return None and the label is kept for display.
    """
    match = _CLOSING_RE.search(label or "")
    if not match:
        return None
    day, month_name, year, hour, minute = match.groups()
    month = _MONTHS.get(month_name.lower())
    if month is None:
        return None
    try:
        eat = timezone(timedelta(hours=3))
        return datetime(
            int(year), month, int(day), int(hour), int(minute), tzinfo=eat
        ).astimezone(UTC)
    except ValueError:
        return None


def _parse_tile(href: str, block: str) -> dict[str, Any]:
    meta_match = re.search(r'<span class="job-tile-meta">(.*?)</span>', block, re.S)
    employer, _, location = _inline(
        meta_match.group(1) if meta_match else ""
    ).partition(" · ")

    title_match = re.search(r'<h3\b[^>]*\btitle="([^"]*)"', block)
    title = (
        html.unescape(title_match.group(1))
        if title_match
        else _inline(re.search(r"<h3[^>]*>(.*?)</h3>", block, re.S).group(1))
    )

    deadline_match = re.search(
        r'<p class="job-tile-deadline">(.*?)</p>', block, re.S
    )
    closing_label = _inline(deadline_match.group(1)) if deadline_match else ""

    tags_start = block.find('<span class="job-tile-tags">')
    tags_end = block.find("<strong>", tags_start)
    tags_html = block[tags_start:tags_end] if tags_start >= 0 else ""
    # Drop the wrapper so the first match is the category, not the wrapper
    # swallowing it as its own text.
    wrapper = '<span class="job-tile-tags">'
    if tags_html.startswith(wrapper):
        tags_html = tags_html[len(wrapper):]

    category = ""
    employer_type = ""
    agency = ""
    salary_disclosed = False
    for attrs, inner in re.findall(r"<span([^>]*)>(.*?)</span>", tags_html, re.S):
        text = _inline(inner)
        if not text:
            continue
        if "source-type-badge" in attrs:
            employer_type, _, agency = text.partition(" · ")
        elif "salary-badge" in attrs:
            salary_disclosed = True
        elif "class=" not in attrs:
            category = text

    slug = href.rstrip("/").rsplit("/", 1)[-1]
    return {
        "slug": slug,
        "source_url": f"{BASE_URL}{href}",
        "title": title.strip(),
        "employer": employer.strip(),
        "location": location.strip(),
        "category": category,
        "employer_type": employer_type.strip(),
        "agency": agency.strip(),
        "salary_disclosed": salary_disclosed,
        "closing_label": closing_label,
        "closing_at": parse_closing(closing_label),
        "summary": "",
        "description": "",
        "seniority": "",
        "work_mode": "",
        "employment_type": "",
        "county": "",
        "location_labels": [location.strip()] if location.strip() else [],
        "is_remote": _looks_remote(location),
        "apply_url": f"{BASE_URL}{href}",
    }


def parse_listing(page: str) -> list[dict[str, Any]]:
    """Roles from a work.ke collection page."""
    roles: list[dict[str, Any]] = []
    for href, block in _TILE_RE.findall(page or ""):
        try:
            role = _parse_tile(href, block)
        except Exception as exc:  # noqa: BLE001 - one broken card, not a page
            log.warning("work.ke: skipped malformed tile %s: %s", href, exc)
            continue
        if role["title"] and role["employer"]:
            roles.append(role)
    return roles


def _looks_remote(location: str) -> bool:
    low = (location or "").lower()
    return "remote" in low or "home based" in low or "home-based" in low


def parse_detail(page: str) -> dict[str, Any]:
    """Extra fields from a work.ke role page.

    Returns seniority, work mode, the employer's official apply URL and the
    plain-English summary work.ke publishes. Missing pieces come back empty;
    the caller keeps the listing data as the fallback.
    """
    seniority = ""
    work_mode = ""
    facts_match = re.search(r'<div class="job-facts">(.*?)</div>', page or "", re.S)
    if facts_match:
        for span in re.findall(r"<span>(.*?)</span>", facts_match.group(1), re.S):
            text = _inline(span)
            left, sep, right = text.partition(" · ")
            if sep and left.lower() in {
                "entry level", "internship", "graduate", "mid level", "senior level"
            }:
                seniority, work_mode = left, right
                break

    apply_url = ""
    for href, inner in _ANCHOR_RE.findall(page or ""):
        if "view advert" not in _inline(inner).lower():
            continue
        if href.startswith("http") and "work.ke" not in href:
            apply_url = href
            break

    lines = _lines(page)
    summary = ""
    description = ""
    try:
        start = lines.index("Role overview")
    except ValueError:
        start = -1
    if start >= 0:
        end = next(
            (
                index
                for index, line in enumerate(lines[start:], start)
                if line in ("How to apply", "This listing is published")
            ),
            len(lines),
        )
        body = [
            line for line in lines[start:end] if line != "Read full role"
        ]
        description = "\n".join(body)
        overview: list[str] = []
        for line in body[1:]:  # skip the "Role overview" heading
            if line.lower() in _SECTION_HEADS:
                break
            overview.append(line)
        summary = " ".join(overview).strip()

    return {
        "seniority": seniority,
        "work_mode": work_mode,
        "apply_url": apply_url,
        "summary": summary,
        "description": description,
    }


# ---------------------------------------------------------------- fetching


async def _get(client: httpx.AsyncClient, url: str) -> str:
    response = await client.get(url, headers={"Accept": "text/html"})
    response.raise_for_status()
    return response.text


async def fetch_entry_level(client: httpx.AsyncClient | None = None) -> dict[str, Any]:
    """Fetch the current entry-level technology roles from work.ke.

    Raises only when every listing page fails; a failed detail page still
    leaves a usable role behind, linked to its work.ke page.
    """
    owns_client = client is None
    if owns_client:
        client = httpx.AsyncClient(
            timeout=20.0,
            follow_redirects=True,
            headers={"User-Agent": USER_AGENT},
        )

    try:
        # Try work.ke's live job index first (complete Kenyan job catalog across all disciplines)
        try:
            res = await client.get(
                f"{BASE_URL}{JOB_INDEX_PATH}",
                headers={"Accept": "application/json, text/plain, */*"},
            )
            if res.status_code == 200:
                raw_items = res.json()
                if isinstance(raw_items, list) and len(raw_items) > 0:
                    now = datetime.now(UTC)
                    roles: list[dict[str, Any]] = []
                    for item in raw_items:
                        slug = item.get("slug") or item.get("id") or ""
                        if not slug:
                            continue
                        closing_at = None
                        raw_closing = item.get("closingAt")
                        if raw_closing:
                            try:
                                closing_at = datetime.fromisoformat(
                                    raw_closing.replace("Z", "+00:00")
                                )
                            except ValueError:
                                pass

                        seniority = item.get("seniority", "")
                        if seniority == "Unknown":
                            seniority = ""
                        employment_type = item.get("employmentType", "")
                        if employment_type == "Unknown":
                            employment_type = ""
                        work_mode = item.get("workMode", "")
                        if work_mode == "Unknown":
                            work_mode = ""

                        location = (item.get("location") or "").strip()
                        county = (item.get("county") or "").strip()
                        location_labels = item.get("locationLabels") or []

                        is_remote = (
                            work_mode.lower() == "remote"
                            or _looks_remote(location)
                            or any(_looks_remote(str(l)) for l in location_labels)
                        )

                        source_url = f"{BASE_URL}/jobs/{slug}"
                        apply_url = source_url

                        roles.append({
                            "slug": slug,
                            "source_url": source_url,
                            "title": item.get("title", "").strip(),
                            "employer": item.get("employer", "").strip(),
                            "location": location,
                            "county": county,
                            "category": (item.get("category") or "").strip(),
                            "employer_type": (item.get("sourceName") or "").strip(),
                            "agency": (item.get("sourceName") or "").strip()
                            if item.get("sourceType") == "recruitment-agency"
                            else "",
                            "salary_disclosed": bool(item.get("salaryDisclosed")),
                            "closing_label": (item.get("closingLabel") or "").strip(),
                            "closing_at": closing_at,
                            "summary": "",
                            "description": "",
                            "seniority": seniority.strip(),
                            "work_mode": work_mode.strip(),
                            "employment_type": employment_type.strip(),
                            "location_labels": location_labels,
                            "is_remote": is_remote,
                            "apply_url": apply_url,
                        })
                    roles.sort(key=_sort_key)
                    return {
                        "roles": roles,
                        "fetched_at": now,
                        "source": "work.ke",
                        "warnings": [],
                    }
        except Exception as exc:  # noqa: BLE001 - fallback to HTML scraping
            log.warning("work.ke job-index.json fetch failed (%s); falling back to html scraping", exc)

        pages = await asyncio.gather(
            *(_get(client, f"{BASE_URL}{path}") for path in LISTING_PATHS),
            return_exceptions=True,
        )

        listed: dict[str, dict[str, Any]] = {}
        errors: list[str] = []
        for path, page in zip(LISTING_PATHS, pages, strict=True):
            if isinstance(page, BaseException):
                errors.append(f"{path}: {type(page).__name__}: {page}")
                log.warning("work.ke: listing %s failed: %s", path, page)
                continue
            for role in parse_listing(page):
                listed.setdefault(role["slug"], role)

        if not listed:
            detail = "; ".join(errors) or "listing pages returned no roles"
            raise RuntimeError(f"work.ke unavailable ({detail})")

        semaphore = asyncio.Semaphore(MAX_DETAIL_FETCHES)

        async def enrich(role: dict[str, Any]) -> dict[str, Any] | None:
            async with semaphore:
                try:
                    detail_html = await _get(client, role["source_url"])
                    role.update(parse_detail(detail_html))
                except Exception as exc:  # noqa: BLE001 - keep the listing card
                    log.warning(
                        "work.ke: detail %s failed: %s", role["source_url"], exc
                    )
            if role.get("seniority") and role["seniority"].lower() not in _ENTRY_LEVEL:
                return None
            if not role.get("apply_url"):
                role["apply_url"] = role["source_url"]
            if role.get("work_mode", "").lower() == "remote":
                role["is_remote"] = True
            return role

        enriched = await asyncio.gather(*(enrich(role) for role in listed.values()))

        roles = [role for role in enriched if role is not None]
        roles.sort(key=_sort_key)
        return {
            "roles": roles,
            "fetched_at": datetime.now(UTC),
            "source": "work.ke",
            "warnings": errors,
        }
    finally:
        if owns_client and client is not None:
            await client.aclose()


def _sort_key(role: dict[str, Any]) -> tuple[int, float, str]:
    """Dated roles first, soonest deadline first, rolling roles last."""
    closing = role.get("closing_at")
    if closing is None:
        return (1, 0.0, role.get("title", "").lower())
    return (0, closing.timestamp(), role.get("title", "").lower())


async def get_entry_level(force: bool = False) -> dict[str, Any]:
    """Cached feed for the dashboard tab; serves the last good copy on failure."""
    global _cache, _cached_at  # noqa: PLW0603
    async with _lock:
        if not force and _cache and time.monotonic() - _cached_at < CACHE_TTL_SECONDS:
            return _cache
        try:
            data = await fetch_entry_level()
        except Exception as exc:
            if _cache is not None:
                log.warning("work.ke refresh failed, serving cached feed: %s", exc)
                return _cache
            raise
        _cache = data
        _cached_at = time.monotonic()
        return data


def reset_cache() -> None:
    """Test helper: forget the cached feed."""
    global _cache, _cached_at  # noqa: PLW0603
    _cache = None
    _cached_at = 0.0
