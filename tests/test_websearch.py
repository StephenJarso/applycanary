"""Tests for the DuckDuckGo web-search connector.

No network. Feeds crafted SERP HTML and JSON-LD payloads straight to the parser
helpers, the same way the rest of the suite validates a board against a captured
HTML shape so a markup change is caught here rather than as an empty poll.
"""

from __future__ import annotations

from app.sources.base import RawJob
from app.sources.websearch import (
    WebSearchSource,
    _parse_jobposting,
    _parse_results,
)

SERP = """
<html><body>
  <a href="https://duckduckgo.com/l/?uddg=https%3A%2F%2Facmecorp.com%2Fjobs%2F42%2Fengineer&rpt=2">
    Senior Backend Engineer at Acme Corp
  </a>
  <span class="result__snippet">Build Python services with a 5-year-old team.</span>
  <a href="https://duckduckgo.com/l/?uddg=https%3A%2F%2Fstablejob.com%2Fdev&rst=1">
    Frontend Engineer
  </a>
  <span class="snippet">Remote React role.</span>
</body></html>
"""


def _uddg(url: str) -> str:
    from urllib.parse import quote

    return f"https://duckduckgo.com/l/?uddg={quote(url, safe='')}"


def test_parse_results_extracts_urls_and_titles() -> None:
    results = _parse_results(SERP, limit=10)
    assert len(results) == 2
    assert results[0][0] == "https://acmecorp.com/jobs/42/engineer"
    assert "Backend Engineer" in results[0][1]
    assert "Acme Corp" in results[0][1]
    assert results[1][0] == "https://stablejob.com/dev"
    assert results[1][1] == "Frontend Engineer"


def test_parse_results_respects_limit() -> None:
    results = _parse_results(SERP, limit=1)
    assert len(results) == 1


def test_parse_results_dedupes() -> None:
    doubled = SERP + SERP
    results = _parse_results(doubled, limit=10)
    urls = {u for u, _, _ in results}
    assert urls == {"https://acmecorp.com/jobs/42/engineer", "https://stablejob.com/dev"}


def test_parse_results_drops_non_http_targets() -> None:
    html = f'<a href="{_uddg("ftp://evil")}">bad</a><a href="{_uddg("not-a-url")}">bad</a>'
    assert _parse_results(html, limit=10) == []


def test_source_matches_by_search_terms() -> None:
    src = WebSearchSource(search_terms=["python", "react"])
    assert src._matches(RawJob(source="websearch", source_id="1", company="x",
                               title="Backend Engineer")) is False
    matched = RawJob(source="websearch", source_id="1", company="x",
                     title="Backend Engineer", description="python and react")
    assert src._matches(matched) is True


def test_source_matches_when_no_terms() -> None:
    src = WebSearchSource()
    assert src._matches(RawJob(source="websearch", source_id="1", company="x",
                               title="Anything")) is True


def test_q_uses_terms_or_query() -> None:
    assert WebSearchSource(search_terms=["python", "django"])._q() == "python django jobs"
    assert WebSearchSource(query="staff engineer @acme.com")._q() == "staff engineer @acme.com"
    assert WebSearchSource()._q() == "software engineer jobs"


JSON_LD = '''
<script type="application/ld+json">
{
  "@context": "https://schema.org/",
  "@type": "JobPosting",
  "title": "Senior Backend Engineer",
  "datePosted": "2026-08-07",
  "hiringOrganization": {"name": "Acme Corp"},
  "jobLocation": {"address": {"addressLocality": "Berlin", "addressCountry": "DE"}},
  "description": "<p>Build services in Go.</p>",
  "employmentType": "FULL_TIME"
}
</script>
'''


def test_parse_jobposting_extracts_json_ld() -> None:
    job = _parse_jobposting(
        '<html><head>' + JSON_LD + '</head></html>',
        url="https://acmecorp.com/jobs/42",
        title_hint="",
    )
    assert job is not None
    assert job.source == "websearch"
    assert job.title == "Senior Backend Engineer"
    assert job.company == "Acme Corp"
    assert job.location == "Berlin, DE"
    assert job.posted_at is not None and job.posted_at.year == 2026
    assert "Build services in Go" in job.description
    assert "<p>" not in job.description
    assert job.extra["tags"] == "FULL_TIME"


def test_parse_jobposting_handles_graph_wrappers() -> None:
    graph = '{"@graph":[{"@type":"Organization","name":"Acme"},{"@type":"JobPosting","title":"Backend","datePosted":"2026-08-07"}]}'
    html = f'<script type="application/ld+json">{graph}</script>'
    job = _parse_jobposting(html, url="https://acme.com/jobs/x", title_hint="Backend")
    assert job is not None
    assert job.title == "Backend"
    # No hiringOrganization on the posting -> company falls back to the host.
    assert job.company == "acme.com"


def test_parse_jobposting_returns_none_when_no_json_ld() -> None:
    assert _parse_jobposting("<html><body>no structured data</body></html>",
                             url="https://acme.com/x", title_hint="X") is None
