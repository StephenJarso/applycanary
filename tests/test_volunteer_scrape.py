"""Tests for the daily tech-event volunteer scrape.

All pure or stubbed: the suite never reaches DuckDuckGo, WordPress or any
conference site. What is pinned here is the two things that broke silently
before — the SERP parser and the volunteer filter — plus the card shape the
volunteer tab renders.
"""

from __future__ import annotations

from datetime import date, timedelta

from app.scraper.opportunity_scraper import (
    _is_tech_volunteer,
    _parse_ddg_lite,
    _parse_rss,
    _recently_published,
    _scrape_tech_volunteers,
)

SERP_HTML = """
<div class="result">
  <a rel="nofollow" href="//duckduckgo.com/l/?uddg=https%3A%2F%2F2027.djangocon.eu%2Fabout%2Fvolunteers%2F&amp;rut=abc"
     class="result__a">Volunteers - DjangoCon Europe 2027</a>
</div>
<div class="result">
  <a rel="nofollow" href="//duckduckgo.com/l/?uddg=https%3A%2F%2F2027.djangocon.eu%2Fabout%2Fvolunteers%2F&amp;rut=def"
     class="result__a">Volunteers - DjangoCon Europe 2027</a>
</div>
<div class="result">
  <a rel="nofollow" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fvolunteers.fosdem.org%2F"
     class="result__a">FOSDEM Volunteers</a>
</div>
"""

RSS_XML = """<?xml version="1.0"?>
<rss version="2.0"><channel>
  <item>
    <title>DevFest Kisumu 2027: call for volunteers</title>
    <link>https://example.org/devfest-kisumu-volunteers/</link>
    <description>We need a crew for registration and a developer conference room.</description>
    <pubDate>Mon, 02 Nov 2026 09:00:00 +0000</pubDate>
  </item>
  <item>
    <title>Scholarship for African students 2027</title>
    <link>https://example.org/scholarship/</link>
    <description>Fully funded technology scholarship.</description>
    <pubDate>Mon, 02 Nov 2026 09:00:00 +0000</pubDate>
  </item>
</channel></rss>
"""


class _FakeResponse:
    def __init__(self, text: str, status_code: int = 200) -> None:
        self.text = text
        self.status_code = status_code


def test_ddg_lite_parser_recovers_target_urls_and_dedupes():
    results = _parse_ddg_lite(SERP_HTML, limit=10)

    assert [url for url, _ in results] == [
        "https://2027.djangocon.eu/about/volunteers/",
        "https://volunteers.fosdem.org/",
    ]
    assert results[0][1] == "Volunteers - DjangoCon Europe 2027"


def test_ddg_lite_parser_ignores_a_challenge_page():
    assert _parse_ddg_lite("<html>Select all squares containing a duck</html>", limit=5) == []


def test_rss_parser_carries_the_publication_date():
    entries = _parse_rss(RSS_XML, max_entries=10)

    assert len(entries) == 2
    assert entries[0]["published"].startswith("Mon, 02 Nov 2026")
    assert _recently_published(entries[0]["published"]) is True


def test_old_publications_are_dropped_but_undated_ones_survive():
    old = (date.today() - timedelta(days=800)).strftime("%a, %d %b %Y 09:00:00 +0000")
    assert _recently_published(old) is False
    assert _recently_published("") is True


def test_a_crew_call_needs_volunteers_in_the_headline_and_tech_in_the_text():
    assert _is_tech_volunteer(
        "DevFest Kisumu 2027: call for volunteers", "developer conference crew"
    )
    # A scholarship post that mentions tech is not a volunteer role.
    assert not _is_tech_volunteer(
        "Scholarship for African students 2027", "fully funded technology scholarship"
    )
    # A medical conference crew is not a developer opportunity.
    assert not _is_tech_volunteer(
        "Call for volunteers", "nurses and medical students for the clinic"
    )


def test_scrape_returns_cards_from_feeds_and_skips_a_blocked_search(monkeypatch):
    calls: list[str] = []

    def fake_get(url, **kwargs):  # noqa: ANN001, ARG001
        calls.append(url)
        if "central.wordcamp.org" in url:
            return _FakeResponse(RSS_XML)
        if "opportunitiesforafricans" in url:
            return _FakeResponse("")
        # DuckDuckGo's bot challenge: the scraper must stop after this one.
        return _FakeResponse("Select all squares containing a duck", status_code=202)

    monkeypatch.setattr("app.scraper.opportunity_scraper._get", fake_get)

    items = _scrape_tech_volunteers()

    assert [item["title"] for item in items] == ["DevFest Kisumu 2027: call for volunteers"]
    item = items[0]
    assert item["kind"] == "Volunteer"
    assert item["url"] == "https://example.org/devfest-kisumu-volunteers/"
    assert "volunteer" in item["tags"] and "tech events" in item["tags"]
    assert item["steps"] and item["when"] and item["city"]
    # One challenge is enough to skip the remaining queries.
    ddg_calls = [url for url in calls if "duckduckgo" in url]
    assert len(ddg_calls) == 1


def test_scrape_keeps_a_conference_crew_found_by_search(monkeypatch):
    def fake_get(url, **kwargs):  # noqa: ANN001, ARG001
        if "central.wordcamp.org" in url or "opportunitiesforafricans" in url:
            return _FakeResponse("<rss><channel></channel></rss>")
        return _FakeResponse(SERP_HTML)

    monkeypatch.setattr("app.scraper.opportunity_scraper._get", fake_get)

    urls = [item["url"] for item in _scrape_tech_volunteers()]

    assert "https://2027.djangocon.eu/about/volunteers/" in urls
    # "FOSDEM Volunteers" has no tech word in the title or URL, so it is not
    # enough on its own; the query itself is not evidence.
    assert "https://volunteers.fosdem.org/" not in urls


def test_scrape_survives_every_source_failing(monkeypatch):
    def fake_get(url, **kwargs):  # noqa: ANN001, ARG001
        return None  # what _get itself returns after a network error

    monkeypatch.setattr("app.scraper.opportunity_scraper._get", fake_get)

    assert _scrape_tech_volunteers() == []
