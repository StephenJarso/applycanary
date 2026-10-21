"""Tests for the work.ke entry-level feed.

The fixtures are trimmed copies of work.ke's rendered markup: a collection page
with one card per role, and a detail page carrying the seniority, the employer's
official apply link and work.ke's plain-English summary. Nothing here reaches
the network — the fetch tests use httpx's MockTransport.
"""

from __future__ import annotations

from datetime import UTC, datetime

import httpx
import pytest
from fastapi.testclient import TestClient

import app.entry_level as entry_level
from app.entry_level import (
    fetch_entry_level,
    parse_closing,
    parse_detail,
    parse_listing,
)
from app.main import create_app
from app.sources.workke import WorkKESource

LISTING_HTML = """
<div class="catalog-job-grid">
  <a class="job-tile" href="/jobs/junior-it-officer-example-abc123">
    <span class="job-tile-meta">Example Co<!-- --> · <!-- -->Nairobi</span>
    <h3 title="Junior IT Officer">Junior IT Officer</h3>
    <p class="job-tile-deadline">Closes Thursday, 29 October 2026, 23:59 EAT</p>
    <span class="job-tile-tags">
      <span>Information Technology</span>
      <span class="source-type-badge source-type-direct-employer">Direct employer</span>
      <span class="salary-badge">Salary disclosed</span>
    </span>
    <strong>View role ↗</strong>
  </a>
  <a class="job-tile" href="/jobs/service-desk-example-def456">
    <span class="job-tile-meta">Employer not disclosed by agency<!-- --> · <!-- -->Mombasa</span>
    <h3 title="Service Desk Assistant">Service Desk Assistant</h3>
    <p class="job-tile-deadline">Deadline not stated by agency</p>
    <span class="job-tile-tags">
      <span>Information Technology</span>
      <span class="source-type-badge source-type-recruitment-agency">Recruitment agency<!-- --> · <!-- -->Stratostaff East Africa</span>
    </span>
    <strong>View role ↗</strong>
  </a>
  <a class="job-tile" href="/jobs/platform-engineer-example-ghi789">
    <span class="job-tile-meta">Example Co<!-- --> · <!-- -->Nairobi</span>
    <h3 title="Platform Engineer">Platform Engineer</h3>
    <p class="job-tile-deadline">Deadline not published by employer</p>
    <span class="job-tile-tags">
      <span>Information Technology</span>
      <span class="source-type-badge source-type-direct-employer">Direct employer</span>
    </span>
    <strong>View role ↗</strong>
  </a>
</div>
"""

ENTRY_DETAIL_HTML = """
<div class="job-detail-labels"><p class="job-category">Information Technology<!-- --> · <!-- -->Full-time</p></div>
<h1>Junior IT Officer</h1>
<div class="job-facts">
  <span><svg aria-hidden="true"></svg>Nairobi</span>
  <span>Entry level<!-- --> · <!-- -->Remote</span>
  <span>Deadline not stated by agency</span>
  <span>Checked 21 Oct 2026</span>
</div>
<a href="https://employer.example/apply/42">View advert &amp; apply</a>
<section>
  <h2>About this role</h2>
  <p class="job-rewrite-note">This plain-English summary was prepared by work.ke.</p>
  <div class="role-description">
    <section><h3>Role overview</h3><div><p>Provide first-line support to staff
      and keep the IT asset register current.</p></div></section>
    <details><summary>Read full role</summary>
      <section><h3>What you will do</h3><ul><li>Resolve hardware and software issues.</li></ul></section>
      <section><h3>Requirements</h3><ul><li>Degree in IT or computer science.</li></ul></section>
    </details>
  </div>
</section>
<h2>How to apply</h2>
<p>Apply through the employer's official advert.</p>
"""

MID_LEVEL_DETAIL_HTML = """
<div class="job-facts">
  <span>Nairobi</span>
  <span>Mid level<!-- --> · <!-- -->Not stated</span>
</div>
<a href="https://employer.example/apply/99">View advert &amp; apply</a>
<section><h2>About this role</h2><div class="role-description">
  <h3>Role overview</h3><p>Run the platform.</p>
</div></section>
<h2>How to apply</h2>
"""


def _transport() -> httpx.MockTransport:
    """Route the listing pages and detail pages to the fixtures above."""

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path in ("/jobs/entry-level-technology", "/jobs/junior-tech"):
            return httpx.Response(200, text=LISTING_HTML)
        if path.endswith("abc123"):
            return httpx.Response(200, text=ENTRY_DETAIL_HTML)
        if path.endswith("def456"):
            return httpx.Response(
                200,
                text=ENTRY_DETAIL_HTML.replace(
                    "Entry level<!-- --> · <!-- -->Remote", "Entry level<!-- --> · <!-- -->Not stated"
                ),
            )
        if path.endswith("ghi789"):
            return httpx.Response(200, text=MID_LEVEL_DETAIL_HTML)
        return httpx.Response(404, text="not found")

    return httpx.MockTransport(handler)


def test_parse_closing_converts_eat_to_utc() -> None:
    parsed = parse_closing("Closes Thursday, 29 October 2026, 23:59 EAT")
    assert parsed == datetime(2026, 10, 29, 20, 59, tzinfo=UTC)
    assert parse_closing("Deadline not published by employer") is None
    assert parse_closing("Deadline not stated by agency") is None


def test_parse_listing_reads_employer_location_and_provenance() -> None:
    roles = parse_listing(LISTING_HTML)
    assert [role["slug"] for role in roles] == [
        "junior-it-officer-example-abc123",
        "service-desk-example-def456",
        "platform-engineer-example-ghi789",
    ]

    first = roles[0]
    assert first["title"] == "Junior IT Officer"
    assert first["employer"] == "Example Co"
    assert first["location"] == "Nairobi"
    assert first["category"] == "Information Technology"
    assert first["employer_type"] == "Direct employer"
    assert first["salary_disclosed"] is True
    assert first["apply_url"] == first["source_url"]

    second = roles[1]
    assert second["employer_type"] == "Recruitment agency"
    assert second["agency"] == "Stratostaff East Africa"
    assert second["closing_at"] is None
    assert second["closing_label"] == "Deadline not stated by agency"


def test_parse_detail_extracts_seniority_apply_link_and_summary() -> None:
    detail = parse_detail(ENTRY_DETAIL_HTML)
    assert detail["seniority"] == "Entry level"
    assert detail["work_mode"] == "Remote"
    assert detail["apply_url"] == "https://employer.example/apply/42"
    assert detail["summary"] == (
        "Provide first-line support to staff and keep the IT asset register current."
    )
    assert "Requirements" in detail["description"]
    assert "Read full role" not in detail["description"]


async def test_fetch_keeps_only_entry_level_roles_and_points_at_employer() -> None:
    async with httpx.AsyncClient(transport=_transport()) as client:
        feed = await fetch_entry_level(client)

    by_slug = {role["slug"]: role for role in feed["roles"]}
    assert "platform-engineer-example-ghi789" not in by_slug
    assert set(by_slug) == {
        "junior-it-officer-example-abc123",
        "service-desk-example-def456",
    }

    first = by_slug["junior-it-officer-example-abc123"]
    assert first["apply_url"] == "https://employer.example/apply/42"
    assert first["source_url"] == (
        "https://work.ke/jobs/junior-it-officer-example-abc123"
    )
    assert first["closing_at"] == datetime(2026, 10, 29, 20, 59, tzinfo=UTC)
    assert first["is_remote"] is True
    assert feed["source"] == "work.ke"

    # Dated roles sort before rolling ones.
    assert feed["roles"][0]["slug"] == "junior-it-officer-example-abc123"


async def test_fetch_raises_when_no_listing_page_answers() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("network down", request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(RuntimeError, match="work.ke unavailable"):
            await fetch_entry_level(client)


async def test_source_turns_roles_into_raw_jobs() -> None:
    async with (
        httpx.AsyncClient(transport=_transport()) as client,
        WorkKESource(client=client) as source,
    ):
        jobs = await source.fetch()

    assert {job.source_id for job in jobs} == {
        "junior-it-officer-example-abc123",
        "service-desk-example-def456",
    }
    assert all(job.source == "workke" for job in jobs)
    assert all(job.apply_url.startswith("https://employer.example/") for job in jobs)
    assert all(job.description for job in jobs)


def test_public_entry_level_endpoint_serves_the_feed(monkeypatch: pytest.MonkeyPatch) -> None:
    entry_level.reset_cache()

    async def fake_fetch() -> dict:
        return {
            "roles": [
                {
                    "slug": "junior-it-officer-example-abc123",
                    "title": "Junior IT Officer",
                    "employer": "Example Co",
                    "location": "Nairobi",
                    "category": "Information Technology",
                    "employer_type": "Direct employer",
                    "agency": "",
                    "seniority": "Entry level",
                    "work_mode": "Remote",
                    "is_remote": True,
                    "salary_disclosed": True,
                    "closing_at": datetime(2026, 10, 29, 20, 59, tzinfo=UTC),
                    "closing_label": "Closes Thursday, 29 October 2026, 23:59 EAT",
                    "summary": "Provide first-line support.",
                    "apply_url": "https://employer.example/apply/42",
                    "source_url": "https://work.ke/jobs/junior-it-officer-example-abc123",
                }
            ],
            "fetched_at": datetime(2026, 10, 21, 9, 0, tzinfo=UTC),
            "source": "work.ke",
            "warnings": [],
        }

    monkeypatch.setattr(entry_level, "fetch_entry_level", fake_fetch)
    entry_level.reset_cache()

    client = TestClient(create_app(), follow_redirects=False)
    response = client.get("/api/public/entry-level")

    assert response.status_code == 200
    body = response.json()
    assert body["source"] == "work.ke"
    assert body["roles"][0]["apply_url"] == "https://employer.example/apply/42"
