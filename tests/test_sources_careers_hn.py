"""Parsing tests for the official-careers and HN "Who is hiring?" connectors.

Both read free-form pages, so the fixtures preserve the shapes that matter:
HENNGE's WordPress pages (open-graph/title, no JSON-LD) and an HN comment whose
first paragraph is the only structured part of the posting.
"""

from __future__ import annotations

import httpx

from app.sources.careers import (
    OfficialCareersSource,
    _clean_title,
    _select_sitemap_children,
    _sitemap_urls,
)
from app.sources.hn_hiring import HNHiringSource, _split_header


class TestCareersPageSelection:
    def _src(self, **over):
        base = {
            "company_name": "HENNGE",
            "location": "Tokyo, Japan",
            "include": ["/en/mid-career-ngh/", "/en/gip/"],
            "exclude": ["/apply/", "/faq", "/gip-tgt"],
            "exclude_exact": ["https://recruit.hennge.com/en/mid-career-ngh/"],
        }
        return OfficialCareersSource(**base | over)

    def test_sitemap_urls_are_filtered_and_deduped(self):
        src = self._src()
        urls = [
            "https://recruit.hennge.com/en/mid-career-ngh/",
            "https://recruit.hennge.com/en/mid-career-ngh/backend-engineer/",
            "https://recruit.hennge.com/en/gip/",
            "https://recruit.hennge.com/en/gip/apply/",
            "https://recruit.hennge.com/en/gip-tgt-terms/",
            "https://recruit.hennge.com/en/hello-world/",
            "https://recruit.hennge.com/en/mid-career-ngh/backend-engineer/",
        ]
        assert src._select_urls(urls) == [
            "https://recruit.hennge.com/en/mid-career-ngh/backend-engineer/",
            "https://recruit.hennge.com/en/gip/",
        ]

    def test_sitemap_index_children_detected(self):
        index = (
            '<?xml version="1.0"?><sitemapindex>'
            "<sitemap><loc>https://example.test/a.xml</loc></sitemap>"
            "<sitemap><loc>https://example.test/b.xml</loc></sitemap>"
            "</sitemapindex>"
        )
        assert _select_sitemap_children(index) == [
            "https://example.test/a.xml",
            "https://example.test/b.xml",
        ]
        assert _select_sitemap_children("<urlset><url><loc>x</loc></url></urlset>") == []

    def test_sitemap_urls_reads_locs(self):
        xml = "<urlset><url><loc> https://a.test/job </loc></url></urlset>"
        assert _sitemap_urls(xml) == ["https://a.test/job"]

    async def test_sitemap_index_children_are_followed(self):
        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path.endswith("wp-sitemap.xml"):
                return httpx.Response(
                    200,
                    text=(
                        "<sitemapindex><sitemap><loc>https://recruit.hennge.com/en/sitemap.xml</loc>"
                        "</sitemap></sitemapindex>"
                    ),
                )
            return httpx.Response(
                200,
                text=(
                    "<urlset><url><loc>https://recruit.hennge.com/en/gip/</loc></url>"
                    "<url><loc>https://recruit.hennge.com/en/gip-tgt/</loc></url></urlset>"
                ),
            )

        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        src = self._src(sitemap="https://recruit.hennge.com/wp-sitemap.xml")
        src._client = client
        try:
            assert await src._page_urls() == ["https://recruit.hennge.com/en/gip/"]
        finally:
            await client.aclose()


class TestCareersParsing:
    def test_jsonld_posting(self):
        html = """
        <html><head><title>Ignored</title>
        <script type="application/ld+json">{"@context":"https://schema.org","@type":"JobPosting",
        "title":"Backend Engineer","datePosted":"2026-08-01T09:00:00Z",
        "hiringOrganization":{"@type":"Organization","name":"HENNGE"},
        "jobLocation":{"@type":"Place","address":{"addressLocality":"Tokyo","addressCountry":"JP"}},
        "description":"<p>Build Go services.</p>"}</script></head>
        <body><a href="/en/mid-career/apply/?gh_jid=42">Apply now</a></body></html>
        """
        src = OfficialCareersSource(company_name="HENNGE", location="Tokyo, Japan")
        job = src._parse(html, "https://recruit.hennge.com/en/mid-career-ngh/backend/")
        assert job.title == "Backend Engineer"
        assert job.company == "HENNGE"
        assert job.location == "Tokyo, JP"
        assert "Go services" in job.description
        assert "gh_jid=42" in job.apply_url
        assert job.extra["official"] is True

    def test_when_wordpress_page_has_no_jsonld(self):
        html = """
        <html><head>
          <title>GLOBAL INTERNSHIP PROGRAM | HENNGE Careers</title>
          <meta name="description" content="A paid internship in Tokyo.">
          <meta property="og:title" content="GLOBAL INTERNSHIP PROGRAM">
        </head><body>
          <h1>APPLY</h1>
          <main><p>Work on real Go and Python projects.</p></main>
          <a href="https://challenge.hennge.com/?entry-code=GIPFE-HENNGEWEBPAGE">Apply</a>
        </body></html>
        """
        src = OfficialCareersSource(company_name="HENNGE", location="Tokyo, Japan")
        job = src._parse(html, "https://recruit.hennge.com/en/gip/")
        assert job.title == "GLOBAL INTERNSHIP PROGRAM"
        assert job.location == "Tokyo, Japan"
        assert "paid internship" in job.description
        assert job.apply_url == "https://challenge.hennge.com/?entry-code=GIPFE-HENNGEWEBPAGE"

    def test_generic_apply_page_is_not_a_job(self):
        html = "<html><head><title>APPLY | HENNGE</title></head><body>Apply</body></html>"
        src = OfficialCareersSource(company_name="HENNGE")
        assert src._parse(html, "https://recruit.hennge.com/en/apply/") is None

    def test_title_suffix_removed(self):
        assert _clean_title("Backend Engineer | HENNGE Careers") == "Backend Engineer"


class TestHNHiringParsing:
    def _comment(self, **over):
        return {
            "id": 123,
            "created_at_i": 1786136428,
            "text": (
                "Acme | Backend Engineer (Go) | Remote (US)"
                "<p>We build a distributed database in Go. "
                '<a href="https://acme.test/careers">careers</a></p>'
            ),
        } | over

    def test_parses_pipe_header(self):
        job = HNHiringSource()._parse(self._comment())
        assert job.company == "Acme"
        assert job.title == "Backend Engineer (Go)"
        assert job.location == "Remote (US)"
        assert job.apply_url == "https://acme.test/careers"
        assert job.posted_at is not None and job.posted_at.year == 2026
        assert job.extra["official"] is True

    def test_parses_dash_header_with_parenthetical_location(self):
        comment = self._comment(
            text="Acme (Tokyo) — Go Engineer<p>Kubernetes and Go.</p>"
        )
        job = HNHiringSource()._parse(comment)
        assert job.company == "Acme"
        assert job.title == "Go Engineer"
        assert job.location == "Tokyo"

    def test_company_falls_back_to_link_domain(self):
        comment = self._comment(
            text="Hiring a Go engineer.<p><a href=\"https://www.example.dev/jobs\">Jobs</a></p>"
        )
        job = HNHiringSource()._parse(comment)
        assert job.company == "example.dev"
        assert job.apply_url == "https://www.example.dev/jobs"

    def test_search_terms_filter(self):
        src = HNHiringSource(search_terms=["rust"])
        assert src._parse(self._comment()) is None

    def test_split_header_variants(self):
        assert _split_header("Acme | Platform Engineer | Berlin") == (
            "Acme", "Platform Engineer", "Berlin",
        )
        assert _split_header("Acme | Berlin | Platform Engineer") == (
            "Acme", "Platform Engineer", "Berlin",
        )

    async def test_fetch_reads_latest_thread(self):
        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path.endswith("/search_by_date"):
                return httpx.Response(200, json={"hits": [
                    {"objectID": "1", "title": "Ask HN: Who is hiring? (October 2026)"},
                ]})
            return httpx.Response(200, json={"children": [self._comment()]})

        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        src = HNHiringSource(client=client)
        try:
            jobs = await src.fetch()
        finally:
            await client.aclose()
        assert len(jobs) == 1
        assert jobs[0].title == "Backend Engineer (Go)"


class TestCareersWiring:
    def test_build_careers_skips_disabled_and_incomplete_entries(self):
        from app.pipeline.ingest import build_careers

        config = {
            "careers": [
                {"name": "HENNGE", "sitemap": "https://example.test/sitemap.xml"},
                {"name": "Disabled", "url": "https://example.test/jobs", "enabled": False},
                {"name": "No URL"},
            ]
        }
        built = build_careers(config, None)  # type: ignore[arg-type]
        assert [s.name for s in built] == ["careers"]
        assert built[0].company_name == "HENNGE"

    def test_registry_has_both_new_sources(self):
        from app.sources import all_sources

        assert "careers" in all_sources()
        assert "hn_hiring" in all_sources()
