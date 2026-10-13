"""Tests for the public Events & paid programs feed.

Two things are pinned here: the parser reads Tech Group Kenya's *actual*
markup (the fixtures below are trimmed copies of their rendered pages), and
the endpoint keeps its promise — it answers 200 with paid programs even when
the live event feed is down, and reports that honestly through `events_live`.

The suite never reaches the network: `refresh_events` is stubbed in every
endpoint test, and the parser tests are pure functions over fixtures.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.opportunities import (
    PAID_PROGRAMS,
    CardEvent,
    Feed,
    _to_item,
    parse_card_date,
    parse_event_cards,
    parse_event_detail,
)

# Trimmed copies of https://events.techgroupkenya.co.ke markup: the index page
# renders whole cards server-side, and each event's own page carries a
# schema.org/Event JSON-LD block.
INDEX_HTML = """
<div class="events-grid">
  <a href="https://events.techgroupkenya.co.ke/events/ADM-TD6BH979E1/gophercon-africa-2026/"
     class="event-card" data-event-id="649" data-ssr="1">
    <div class="event-image">
      <img width="400" height="250" src="https://cdn.example/gopher.jpg"
           class="attachment-event-card size-event-card wp-post-image" alt="GopherCon" />
      <span class="event-category">Tech Careers &amp; Networking</span>
    </div>
    <div class="event-content">
      <div class="event-meta-row">
        <span><i class="fas fa-calendar" aria-hidden="true"></i> Oct 15, 2026</span>
        <span><i class="fas fa-map-marker-alt" aria-hidden="true"></i> Nairobi</span>
      </div>
      <h3 class="event-title">GOPHERCON AFRICA 2026</h3>
    </div>
    <div class="event-footer">
      <span class="details-btn btn btn-primary">View Details</span>
    </div>
  </a>
  <a href="https://events.techgroupkenya.co.ke/events/TGK26I47W3/ciris-tech-2026/"
     class="event-card" data-event-id="967" data-ssr="1">
    <div class="event-image">
      <span class="event-category">Tech Startups &amp; Business</span>
    </div>
    <div class="event-content">
      <div class="event-meta-row">
        <span><i class="fas fa-calendar" aria-hidden="true"></i> Oct 14, 2026</span>
        <span><i class="fas fa-map-marker-alt" aria-hidden="true"></i> Uasin Gishu</span>
      </div>
      <h3 class="event-title">CIRIS Tech 2026</h3>
    </div>
  </a>
</div>
"""

DETAIL_HTML = """
<html><head>
<script type="application/ld+json">
{"@context": "https://schema.org", "@type": "Event", "name": "GOPHERCON AFRICA 2026",
 "description": "Join Africa's largest Go conference! <b>Two days</b> of talks in Nairobi.",
 "url": "https://events.techgroupkenya.co.ke/events/ADM-TD6BH979E1/gophercon-africa-2026/",
 "eventStatus": "https://schema.org/EventScheduled",
 "eventAttendanceMode": "https://schema.org/OfflineEventAttendanceMode",
 "organizer": {"@type": "Organization", "name": "GDG Nairobi", "url": "https://example.org"},
 "startDate": "2026-10-15T08:00:00+03:00",
 "endDate": "2026-10-15T11:00:00+03:00",
 "offers": {"@type": "Offer", "price": 1500, "priceCurrency": "KES"},
 "location": {"@type": "Place", "name": "Nairobi",
              "address": {"@type": "PostalAddress", "addressLocality": "Nairobi",
                          "addressCountry": "KE"}}}
</script>
<script type="application/ld+json">
{"@context": "https://schema.org", "@type": "Organization", "name": "Not the event"}
</script>
</head><body></body></html>
"""


# ------------------------------------------------------------------ parsing


def test_parse_event_cards_reads_the_rendered_fields():
    cards = parse_event_cards(INDEX_HTML)
    assert [c.title for c in cards] == ["GOPHERCON AFRICA 2026", "CIRIS Tech 2026"]

    gopher = cards[0]
    assert gopher.url.endswith("/gophercon-africa-2026/")
    assert gopher.date_text == "Oct 15, 2026"
    assert gopher.location == "Nairobi"
    # Character references are decoded, not shipped to the UI.
    assert gopher.category == "Tech Careers & Networking"


def test_parse_event_cards_skips_malformed_cards():
    page = (
        '<a href="https://events.techgroupkenya.co.ke/events/x/y/" class="event-card">'
        '<h3 class="event-title">Missing date markup</h3></a>'
        '<a href="" class="event-card"><h3 class="event-title">No URL</h3></a>'
    )
    cards = parse_event_cards(page)
    assert [c.title for c in cards] == ["Missing date markup"]
    assert cards[0].date_text == "" and cards[0].location == ""


def test_parse_card_date_is_locale_independent():
    assert parse_card_date("Oct 15, 2026") == "2026-10-15"
    assert parse_card_date("Feb 1, 2027") == "2027-02-01"
    assert parse_card_date("") == ""
    assert parse_card_date("Date TBA") == ""
    assert parse_card_date("Oct 32, 2026") == ""


def test_parse_event_detail_reads_the_event_jsonld():
    detail = parse_event_detail(DETAIL_HTML)
    assert detail is not None
    # Markup inside the description is stripped.
    assert detail.description == "Join Africa's largest Go conference! Two days of talks in Nairobi."
    assert detail.organizer == "GDG Nairobi"
    assert detail.start == datetime(2026, 10, 15, 8, 0, tzinfo=timezone(timedelta(hours=3)))
    assert detail.end == datetime(2026, 10, 15, 11, 0, tzinfo=timezone(timedelta(hours=3)))
    assert detail.city == "Nairobi"
    assert detail.price == 1500.0
    assert detail.currency == "KES"
    assert detail.online is False


def test_parse_event_detail_ignores_non_event_blocks():
    page = '<script type="application/ld+json">{"@type": "Organization"}</script>'
    assert parse_event_detail(page) is None
    assert parse_event_detail("<html><body>no structured data</body></html>") is None
    assert parse_event_detail('<script type="application/ld+json">{not json</script>') is None


def test_event_item_combines_card_and_detail():
    card = parse_event_cards(INDEX_HTML)[0]
    item = _to_item(card, parse_event_detail(DETAIL_HTML))
    assert item["kind"] == "Event"
    assert item["date"] == "2026-10-15"
    assert item["when"] == "15 Oct 2026 · 08:00–11:00"
    assert item["city"] == "Nairobi"
    assert item["org"] == "GDG Nairobi"
    assert item["tags"] == ["Tech Careers & Networking"]
    assert item["source"] == "TGK Events"
    # A ticketed event shows its price — see the free-event case below.
    assert item["stipend"] == "Ticket 1,500 KES"


def test_free_event_prints_no_price():
    card = parse_event_cards(INDEX_HTML)[0]
    detail = parse_event_detail(DETAIL_HTML.replace('"price": 1500', '"price": 0'))
    assert detail is not None
    assert _to_item(card, detail)["stipend"] is None


def test_event_item_falls_back_to_the_card_when_detail_is_missing():
    card = parse_event_cards(INDEX_HTML)[0]
    item = _to_item(card, None)
    assert item["date"] == "2026-10-15"
    # The card only prints a date, so that is what ships.
    assert item["when"] == "Oct 15, 2026"
    assert item["org"] == "Tech Group Kenya"
    assert item["desc"] == ""


def test_online_event_is_bucketed_under_online():
    card = CardEvent(
        url="https://events.techgroupkenya.co.ke/events/x/y/",
        title="Virtual Meetup",
        date_text="Nov 3, 2026",
        location="",
        category="Tech Careers & Networking",
    )
    detail = parse_event_detail(
        DETAIL_HTML.replace("OfflineEventAttendanceMode", "OnlineEventAttendanceMode")
    )
    assert detail is not None and detail.online is True
    assert _to_item(card, detail)["city"] == "Online"


# ----------------------------------------------------------------- endpoint


@pytest.fixture
def client() -> TestClient:
    # No context manager on purpose: these routes need no lifespan work, and
    # /api/public/ is whitelisted by the auth middleware, so no session either.
    return TestClient(create_app(), follow_redirects=False)


def _event_item() -> dict:
    return _to_item(parse_event_cards(INDEX_HTML)[0], None)


def test_endpoint_serves_events_and_programs_sorted_by_deadline(client, monkeypatch):
    event = _event_item()

    async def fake_refresh(force: bool = False) -> Feed:  # noqa: ARG001
        return Feed(
            items=[event],
            fetched_at=1.0,
            fetched_at_wall=datetime(2026, 10, 13, 9, 0, tzinfo=timezone.utc),
            attempted_at=1.0,
            ok=True,
        )

    monkeypatch.setattr("app.opportunities.refresh_events", fake_refresh)

    res = client.get("/api/public/opportunities")
    assert res.status_code == 200
    body = res.json()

    assert body["events_live"] is True
    assert body["events_updated_at"] is not None
    titles = [i["title"] for i in body["items"]]

    # Every curated program rides along with the live events.
    assert {p["title"] for p in PAID_PROGRAMS} <= set(titles)

    # Nearest deadline first: the dated event precedes the 2027 program
    # deadlines, and rolling programs (no dates at all) sort last.
    assert titles[0] == "GOPHERCON AFRICA 2026"
    gsoc = titles.index("Google Summer of Code")
    rolling = titles.index("MLH Fellowship")
    assert gsoc < rolling
    assert all(item["kind"] in ("Event", "Paid program") for item in body["items"])


def test_endpoint_still_serves_programs_when_the_feed_is_down(client, monkeypatch):
    async def fake_refresh(force: bool = False) -> Feed:  # noqa: ARG001
        return Feed(items=[], attempted_at=1.0, ok=False)

    monkeypatch.setattr("app.opportunities.refresh_events", fake_refresh)

    res = client.get("/api/public/opportunities")
    assert res.status_code == 200
    body = res.json()

    assert body["events_live"] is False
    assert body["items"], "paid programs must survive an event-feed outage"
    assert {i["kind"] for i in body["items"]} == {"Paid program"}


def test_endpoint_flags_a_stale_event_list(client, monkeypatch):
    """A failed refresh still serves the last good events — but says so."""
    async def fake_refresh(force: bool = False) -> Feed:  # noqa: ARG001
        return Feed(
            items=[_event_item()],
            fetched_at=1.0,
            fetched_at_wall=datetime(2026, 10, 12, 9, 0, tzinfo=timezone.utc),
            attempted_at=2.0,
            ok=False,
        )

    monkeypatch.setattr("app.opportunities.refresh_events", fake_refresh)

    res = client.get("/api/public/opportunities")
    assert res.status_code == 200
    body = res.json()
    assert body["events_live"] is False
    assert [i["title"] for i in body["items"] if i["kind"] == "Event"] == [
        "GOPHERCON AFRICA 2026"
    ]
    assert body["events_updated_at"] is not None


def test_feed_failure_never_fails_the_endpoint(client, monkeypatch):
    """A network-level failure must not 500 the public page.

    `refresh_events` is the failure boundary: it catches what `_fetch_feed`
    throws and returns a Feed with `ok=False`. If a future edit moves the try/
    except somewhere that lets this escape, this assertion is where it shows.
    """
    monkeypatch.setattr("app.opportunities._feed", Feed())

    async def boom() -> tuple[list[dict], datetime]:
        raise RuntimeError("TGK unreachable")

    monkeypatch.setattr("app.opportunities._fetch_feed", boom)

    res = client.get("/api/public/opportunities")
    assert res.status_code == 200
    body = res.json()
    assert body["events_live"] is False
    assert body["items"], "paid programs must survive a total feed outage"
    assert {i["kind"] for i in body["items"]} == {"Paid program"}
