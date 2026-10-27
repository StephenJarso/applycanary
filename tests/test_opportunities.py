"""Tests for the public Events & paid programs feed.

Two things are pinned here: the parser reads Tech Group Kenya's *actual*
markup (the fixtures below are trimmed copies of their rendered pages), and
the endpoint keeps its promise — it answers 200 with paid programs even when
the live event feed is down, and reports that honestly through `events_live`.

The suite never reaches the network: `refresh_events` is stubbed in every
endpoint test, and the parser tests are pure functions over fixtures.
"""

from __future__ import annotations

import asyncio
import time
from datetime import UTC, datetime, timedelta, timezone

import httpx
import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.opportunities import (
    CURATED_EVENTS,
    INTERNSHIPS,
    PAID_PROGRAMS,
    VOLUNTEER,
    CardEvent,
    Feed,
    _dedupe_events,
    _fetch_feed,
    _fetch_gdg,
    _from_event_feed,
    _gdg_item,
    _to_item,
    _upcoming_curated_events,
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


def test_gdg_item_uses_east_africa_time_and_flags_free_registration():
    item = _gdg_item(
        {
            "title": "Devfest Kisumu 2026",
            "start_date": "2026-11-21T05:00:00Z",
            "end_date": "2026-11-21T14:30:00Z",
            "url": "https://gdg.community.dev/events/details/devfest-kisumu/",
            "description_short": "Devfest Kisumu invites <b>tech enthusiasts</b>.",
            "event_type_title": "Free registration",
        },
        "GDG Kisumu",
        "Kisumu",
    )
    assert item is not None
    assert item["date"] == "2026-11-21"
    assert item["when"] == "21 Nov 2026 · 08:00–17:30"
    assert item["city"] == "Kisumu"
    assert item["org"] == "GDG Kisumu"
    assert item["source"] == "GDG Community"
    assert item["stipend"] == "Free registration"
    assert item["desc"] == "Devfest Kisumu invites tech enthusiasts."


def test_gdg_item_requires_title_url_and_start():
    good = {"title": "Devfest", "start_date": "2026-11-21T05:00:00Z"}
    assert _gdg_item({**good, "url": "https://gdg.community.dev/e/1"}, "GDG", "Kisumu")
    assert _gdg_item({**good, "url": "https://gdg.community.dev/e/1", "title": ""}, "GDG", "Kisumu") is None
    assert _gdg_item({**good, "title": "Devfest"}, "GDG", "Kisumu") is None
    assert _gdg_item({"title": "Devfest", "url": "https://gdg.community.dev/e/1"}, "GDG", "Kisumu") is None


def test_fetch_gdg_keeps_future_events_and_ignores_a_failing_chapter(monkeypatch):
    future = (datetime.now(UTC) + timedelta(days=10)).isoformat().replace("+00:00", "Z")
    past = (datetime.now(UTC) - timedelta(days=10)).isoformat().replace("+00:00", "Z")

    def handler(request: httpx.Request) -> httpx.Response:
        if "841" in request.url.path:
            return httpx.Response(
                200,
                json={
                    "results": [
                        {"title": "Past meetup", "start_date": past, "url": "https://gdg.community.dev/e/past"},
                        {"title": "Devfest Kisumu 2026", "start_date": future,
                         "url": "https://gdg.community.dev/e/devfest"},
                    ]
                },
            )
        return httpx.Response(500, text="boom")

    monkeypatch.setattr("app.opportunities.GDG_CHAPTERS", {841: ("GDG Kisumu", "Kisumu"), 826: ("GDG Nairobi", "Nairobi")})

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await _fetch_gdg(client)

    items = asyncio.run(run())
    assert [item["title"] for item in items] == ["Devfest Kisumu 2026"]
    assert items[0]["city"] == "Kisumu"


def test_dedupe_events_prefers_the_tgk_copy():
    tgk = {"title": "DevFest Kisii 2026", "date": "2026-10-31", "source": "TGK Events"}
    gdg = {"title": "devfest kisii 2026", "date": "2026-10-31", "source": "GDG Community"}
    other = {"title": "Devfest Kisumu 2026", "date": "2026-11-21", "source": "GDG Community"}
    assert _dedupe_events([tgk, gdg, other]) == [tgk, other]


def test_feed_merges_both_calendars_and_keeps_stale_tgk_on_outage(monkeypatch):
    stale = {"title": "Old TGK event", "date": "2026-01-01", "source": "TGK Events"}
    monkeypatch.setattr("app.opportunities._feed", Feed(items=[stale]))

    async def broken_tgk(client):
        raise RuntimeError("tag down")

    async def gdg(client):
        return [{"title": "Devfest Kisumu 2026", "date": "2026-11-21", "source": "GDG Community"}]

    monkeypatch.setattr("app.opportunities._fetch_tgk", broken_tgk)
    monkeypatch.setattr("app.opportunities._fetch_gdg", gdg)
    items, _ = asyncio.run(_fetch_feed())
    assert [item["title"] for item in items] == ["Old TGK event", "Devfest Kisumu 2026"]


def test_feed_raises_only_when_both_calendars_fail(monkeypatch):
    async def boom(client):
        raise RuntimeError("down")

    monkeypatch.setattr("app.opportunities._fetch_tgk", boom)
    monkeypatch.setattr("app.opportunities._fetch_gdg", boom)
    with pytest.raises(RuntimeError, match="down"):
        asyncio.run(_fetch_feed())


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
            fetched_at_wall=datetime(2026, 10, 13, 9, 0, tzinfo=UTC),
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

    # Every curated program rides along with the live events; internships have
    # their own endpoint and tab.
    assert {p["title"] for p in PAID_PROGRAMS} <= set(titles)
    assert not ({i["title"] for i in INTERNSHIPS} & set(titles))
    # Dated African conferences that never reach the community calendars are
    # curated with dates read from their official sites.
    assert {e["title"] for e in _upcoming_curated_events()} <= set(titles)

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
    # Static curated events do not depend on the live feed either, but nothing
    # from TGK/GDG should appear when their fetch failed.
    assert {i["kind"] for i in body["items"]} <= {"Paid program", "Event"}
    assert {i["source"] for i in body["items"] if i["kind"] == "Event"} <= {"Curated"}


def test_endpoint_flags_a_stale_event_list(client, monkeypatch):
    """A failed refresh still serves the last good events — but says so."""
    async def fake_refresh(force: bool = False) -> Feed:  # noqa: ARG001
        return Feed(
            items=[_event_item()],
            fetched_at=1.0,
            fetched_at_wall=datetime(2026, 10, 12, 9, 0, tzinfo=UTC),
            attempted_at=2.0,
            ok=False,
        )

    monkeypatch.setattr("app.opportunities.refresh_events", fake_refresh)

    res = client.get("/api/public/opportunities")
    assert res.status_code == 200
    body = res.json()
    assert body["events_live"] is False
    event_titles = [i["title"] for i in body["items"] if i["kind"] == "Event"]
    assert "GOPHERCON AFRICA 2026" in event_titles
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
    assert {i["kind"] for i in body["items"]} <= {"Paid program", "Event"}


def test_endpoint_waits_at_most_bounded_for_a_slow_feed(client, monkeypatch):
    """A stalled third-party feed degrades the response instead of pinning the page.

    Render learned this the hard way: from a US datacenter the whole cold
    fetch (index timeout + detail waves) outlasted the platform's request
    patience, and the visitor watched the page spin until the browser gave
    up. The request side must cap its wait and answer with the programs and
    `events_live=False` instead.
    """
    monkeypatch.setattr("app.opportunities._feed", Feed())
    monkeypatch.setattr("app.opportunities.COLD_WAIT", 0.5)

    async def stall() -> tuple[list[dict], datetime]:
        await asyncio.sleep(60)
        raise AssertionError("must not complete")  # pragma: no cover

    monkeypatch.setattr("app.opportunities._fetch_feed", stall)

    started = time.monotonic()
    res = client.get("/api/public/opportunities")
    elapsed = time.monotonic() - started

    assert res.status_code == 200
    assert res.json()["events_live"] is False
    assert res.json()["items"], "programs still serve while events are unknown"
    assert elapsed < 5


def test_a_timed_out_fetch_gates_followup_requests(client, monkeypatch):
    """The attempt is stamped at fetch start, so retries share the TTL.

    Otherwise every page view during an outage would restart a doomed cold
    fetch and burn the COLD_WAIT cap once more.
    """
    monkeypatch.setattr("app.opportunities._feed", Feed())
    monkeypatch.setattr("app.opportunities.COLD_WAIT", 0.5)

    async def stall() -> tuple[list[dict], datetime]:
        await asyncio.sleep(60)
        raise AssertionError("must not complete")  # pragma: no cover

    monkeypatch.setattr("app.opportunities._fetch_feed", stall)

    started = time.monotonic()
    res1 = client.get("/api/public/opportunities")
    after_first = time.monotonic() - started

    again = time.monotonic()
    res2 = client.get("/api/public/opportunities")
    second_cost = time.monotonic() - again

    assert res1.status_code == res2.status_code == 200
    assert res2.json()["events_live"] is False
    assert after_first < 5
    # The fast path answers from memory in milliseconds; a restarted slow
    # fetch would cost the full (capped) 0.5 s wait.
    assert second_cost < 0.45


def test_curated_events_carry_iso_dates():
    # A card counts down to this date, so it must be a real ISO day, not prose.
    for item in CURATED_EVENTS:
        datetime.fromisoformat(item["date"])
        assert item["when"]


def test_curated_events_expire_when_they_start(monkeypatch):
    monkeypatch.setattr(
        "app.opportunities.CURATED_EVENTS",
        [
            {**CURATED_EVENTS[0], "title": "Past conf", "date": "2020-01-01"},
            {**CURATED_EVENTS[0], "title": "Future conf", "date": "2030-01-01"},
        ],
    )
    titles = [item["title"] for item in _upcoming_curated_events()]
    assert titles == ["Future conf"]


def test_internships_are_split_from_programs_and_state_compensation():
    titles = [i["title"] for i in INTERNSHIPS]
    assert "HENNGE Global Internship Program (GIP)" in titles
    # No entry is served under both tabs.
    assert not (set(titles) & {p["title"] for p in PAID_PROGRAMS})

    hennge = next(i for i in INTERNSHIPS if i["title"].startswith("HENNGE"))
    assert hennge["kind"] == "Internship"
    assert hennge["city"] == "Tokyo"
    assert hennge["stipend"].startswith("Unpaid + benefits")
    assert "Benefits" in hennge["tags"]

    paid = [i for i in INTERNSHIPS if i["stipend"].startswith("Paid:")]
    assert paid, "at least one paid internship documents its stipend"
    assert all("Paid" in i["tags"] for i in paid)


def test_internship_feed_covers_the_global_schemes_with_compensation():
    """The UN and big-employer schemes advertised off-platform are all here.

    These are the ones users find on LinkedIn and then ask why the app does not
    have them, so a silent drop-out is the failure this pins.
    """
    by_title = {entry["title"]: entry for entry in INTERNSHIPS}
    expected = (
        "UNHCR Internship Programme",
        "UNICEF Internship Programme",
        "WFP Internship Programme",
        "Safaricom Internship Program",
        "Google Software Engineering Internship 2027",
    )
    for title in expected:
        assert title in by_title, f"{title} fell out of the internships feed"

    for entry in by_title.values():
        assert entry["stipend"], f"{entry['title']} leaves compensation unstated"
        assert entry["steps"], f"{entry['title']} never says how to apply"
        assert entry["url"].startswith("https://")

    # The UN schemes are the paid ones, and the card keys its badge off both
    # the string and the tag.
    un = [by_title[title] for title in expected[:3]]
    assert all(entry["stipend"].startswith("Paid:") for entry in un)
    assert all("Paid" in entry["tags"] for entry in un)

    # A dated entry must carry a real ISO deadline: the card counts down to it.
    datetime.fromisoformat(by_title["Google Software Engineering Internship 2027"]["closes"])


def test_internships_endpoint_is_public_and_only_internships(client):
    res = client.get("/api/public/internships")
    assert res.status_code == 200
    body = res.json()

    assert body["items"]
    assert {item["kind"] for item in body["items"]} == {"Internship"}
    assert {item["title"] for item in body["items"]} == {i["title"] for i in INTERNSHIPS}
    assert body["events_live"] is True


# ------------------------------------------------------- volunteer & speaking


def _volunteer_event() -> dict:
    """A live calendar entry whose own description is a call for help."""
    return {
        "kind": "Event",
        "title": "DevFest Kisumu 2026",
        "org": "GDG Kisumu",
        "date": "2026-11-21",
        "when": "21 Nov 2026 · 08:00–17:30",
        "city": "Kisumu",
        "tags": ["GDG", "Community"],
        "source": "GDG Community",
        "url": "https://gdg.community.dev/events/details/devfest-kisumu/",
        "stipend": "Free registration",
        "opens": None,
        "closes": None,
        "steps": None,
        "desc": "We need volunteers for the registration desk, and the call for speakers is still open.",
    }


def test_volunteer_calls_are_not_served_under_another_tab():
    titles = [v["title"] for v in VOLUNTEER]
    assert len(titles) == len(set(titles))
    assert not (set(titles) & {p["title"] for p in PAID_PROGRAMS})
    assert not (set(titles) & {i["title"] for i in INTERNSHIPS})
    assert all(v["kind"] == "Volunteer" for v in VOLUNTEER)


def test_volunteer_entries_carry_everything_the_card_renders():
    for entry in VOLUNTEER:
        assert entry["url"].startswith("https://")
        assert entry["when"] and entry["city"] and entry["desc"] and entry["tags"]
        assert entry["steps"], "a reader needs to know how to actually apply"


def test_volunteer_entries_state_compensation_explicitly():
    """Tech volunteering is unpaid; the card must say so, benefits and all."""
    for entry in VOLUNTEER:
        stipend = entry["stipend"]
        assert stipend, f"{entry['title']} leaves compensation blank"
        assert stipend.startswith(("Unpaid", "Paid")), stipend

    unpaid = [v for v in VOLUNTEER if v["stipend"].startswith("Unpaid")]
    assert unpaid
    # A bare "Unpaid ..." is fine. Saying "+ benefits:" is a promise, so the
    # benefit has to actually be named — the card prints this line verbatim.
    for entry in unpaid:
        stipend = entry["stipend"]
        if "+ benefits:" in stipend:
            assert stipend.partition("+ benefits:")[2].strip(), stipend


def test_volunteer_feed_covers_crews_and_speaking_slots():
    tags = {tag for entry in VOLUNTEER for tag in entry["tags"]}
    assert "Volunteer" in tags
    # The two things this tab exists for: a shift on the day, and a talk slot.
    assert "Registration" in tags
    assert "Speaker" in tags


def test_volunteer_dates_are_iso_so_the_card_can_count_down():
    for entry in VOLUNTEER:
        for key in ("opens", "closes"):
            if entry.get(key):
                datetime.fromisoformat(entry[key])


def test_event_feed_calls_are_retagged_as_volunteer_slots():
    items = _from_event_feed([_volunteer_event()])
    assert len(items) == 1
    item = items[0]
    assert item["kind"] == "Volunteer"
    assert "From event feed" in item["tags"] and "Volunteer" in item["tags"]
    assert "Speaker" in item["tags"]  # the same description carries the CFP
    # The event's own date is kept, so the card still counts down to something.
    assert item["date"] == "2026-11-21"
    assert item["source"] == "GDG Community"


def test_event_feed_ignores_an_event_that_merely_lists_speakers():
    """"50+ speakers" is on every conference page and means nothing here."""
    lineup = {
        **_volunteer_event(),
        "title": "Africa Tech Festival 2026",
        "desc": "Join 50+ industry-leading speakers and 300 exhibitors in Cape Town.",
    }
    assert _from_event_feed([lineup]) == []


def test_event_feed_ignores_an_event_with_no_call_in_it():
    quiet = {**_volunteer_event(), "title": "Monthly meetup", "desc": "Talks and pizza."}
    assert _from_event_feed([quiet]) == []


def test_volunteers_endpoint_is_public_and_serves_only_volunteer_roles(client, monkeypatch):
    async def fake_refresh(force: bool = False) -> Feed:  # noqa: ARG001
        return Feed(items=[_volunteer_event()], fetched_at=1.0, ok=True)

    monkeypatch.setattr("app.opportunities.refresh_events", fake_refresh)

    res = client.get("/api/public/volunteers")
    assert res.status_code == 200
    body = res.json()

    assert body["events_live"] is True
    assert {item["kind"] for item in body["items"]} == {"Volunteer"}
    titles = [item["title"] for item in body["items"]]
    # The curated crew calls ride along...
    assert {v["title"] for v in VOLUNTEER} <= set(titles)
    # ...and so does the live event that asked for help.
    assert "DevFest Kisumu 2026" in titles


def test_volunteers_endpoint_does_not_double_a_curated_entry(client, monkeypatch):
    """The same call read off a calendar must not appear twice."""
    curated = VOLUNTEER[0]
    live = {
        **_volunteer_event(),
        "title": curated["title"],
        "desc": "Volunteers wanted for the crew.",
    }

    async def fake_refresh(force: bool = False) -> Feed:  # noqa: ARG001
        return Feed(items=[live], fetched_at=1.0, ok=True)

    monkeypatch.setattr("app.opportunities.refresh_events", fake_refresh)

    titles = [item["title"] for item in client.get("/api/public/volunteers").json()["items"]]
    assert titles.count(curated["title"]) == 1


def test_volunteers_endpoint_keeps_curated_roles_when_the_calendar_is_down(client, monkeypatch):
    async def fake_refresh(force: bool = False) -> Feed:  # noqa: ARG001
        return Feed(items=[], attempted_at=1.0, ok=False)

    monkeypatch.setattr("app.opportunities.refresh_events", fake_refresh)

    res = client.get("/api/public/volunteers")
    assert res.status_code == 200
    body = res.json()

    assert body["events_live"] is False
    assert {item["title"] for item in body["items"]} == {v["title"] for v in VOLUNTEER}
    assert {item["kind"] for item in body["items"]} == {"Volunteer"}
