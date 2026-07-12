"""Connector parsing tests against recorded payload shapes.

No network: each test feeds a captured-shape payload straight to the parser. The
point is to catch a parser breaking when a board changes its JSON, and to prove
the connectors survive the messy real-world cases — missing fields, nulls, HTML
descriptions, epoch-vs-ISO timestamps.
"""

from __future__ import annotations

from app.sources.ashby import AshbySource, _compensation
from app.sources.base import clean_html
from app.sources.greenhouse import GreenhouseSource, parse_iso
from app.sources.lever import LeverSource
from app.sources.remoteok import RemoteOkSource, _int_or_none


class TestCleanHtml:
    def test_strips_tags_and_keeps_text(self):
        out = clean_html("<p>Build <strong>APIs</strong> in Python.</p>")
        assert "Build" in out and "APIs" in out and "<" not in out

    def test_list_items_become_bullets(self):
        out = clean_html("<ul><li>Python</li><li>Django</li></ul>")
        assert "- Python" in out and "- Django" in out

    def test_drops_script_and_style(self):
        out = clean_html("<p>Real</p><script>evil()</script><style>x{}</style>")
        assert "evil" not in out and "x{}" not in out
        assert "Real" in out

    def test_unescapes_entities(self):
        assert "R&D" in clean_html("<p>R&amp;D team</p>")

    def test_empty_and_plain_text_safe(self):
        assert clean_html("") == ""
        assert "plain" in clean_html("plain text")


class TestParseIso:
    def test_parses_z_suffix_to_naive_utc(self):
        dt = parse_iso("2026-08-07T10:30:00Z")
        assert dt is not None and dt.tzinfo is None and dt.hour == 10

    def test_converts_offset_to_utc(self):
        dt = parse_iso("2026-08-07T12:30:00+02:00")
        assert dt is not None and dt.hour == 10

    def test_bad_input_returns_none(self):
        for bad in ("", None, "not-a-date", 12345):
            assert parse_iso(bad) is None


class TestGreenhouse:
    def _job(self, **over):
        base = {
            "id": 4567,
            "title": "Backend Engineer",
            "absolute_url": "https://boards.greenhouse.io/acme/jobs/4567",
            "location": {"name": "Remote - US"},
            "content": "<p>Build <strong>Python</strong> services.</p>",
            "updated_at": "2026-08-01T09:00:00Z",
            "departments": [{"name": "Engineering"}],
        }
        return base | over

    def test_parses_a_normal_posting(self):
        src = GreenhouseSource(board_token="acme", company_name="Acme")
        job = src._parse(self._job())
        assert job.source == "greenhouse"
        assert job.source_id == "4567"
        assert job.company == "Acme"
        assert job.title == "Backend Engineer"
        assert job.location == "Remote - US"
        assert "Python" in job.description and "<p>" not in job.description
        assert job.ats_platform == "greenhouse"
        assert job.ats_board_token == "acme"

    def test_falls_back_to_offices_for_location(self):
        src = GreenhouseSource(board_token="acme")
        job = src._parse(self._job(location={}, offices=[{"name": "Nairobi"}]))
        assert "Nairobi" in job.location

    def test_company_name_derived_from_token_when_absent(self):
        assert GreenhouseSource(board_token="acme-labs").company_name == "Acme Labs"

    def test_missing_fields_do_not_raise(self):
        src = GreenhouseSource(board_token="acme")
        job = src._parse({"id": 1})
        assert job.title == "" and job.description == ""

    def test_to_job_builds_fingerprint_and_canonical_url(self):
        src = GreenhouseSource(board_token="acme", company_name="Acme")
        job = src._parse(self._job()).to_job()
        assert len(job.fingerprint) == 64
        assert job.canonical_url.startswith("https://boards.greenhouse.io")
        assert job.is_remote is True


class TestLever:
    def _job(self, **over):
        base = {
            "id": "abc-123",
            "text": "Senior Data Engineer",
            "hostedUrl": "https://jobs.lever.co/acme/abc-123",
            "categories": {"location": "Berlin", "commitment": "Full-time"},
            "descriptionPlain": "Own the data platform.",
            "createdAt": 1754006400000,
            "workplaceType": "remote",
            "lists": [{"text": "Requirements", "content": "<li>Spark</li>"}],
        }
        return base | over

    def test_parses_a_normal_posting(self):
        src = LeverSource(board_token="acme", company_name="Acme")
        job = src._parse(self._job())
        assert job.source_id == "abc-123"
        assert job.title == "Senior Data Engineer"
        assert job.location == "Berlin"
        assert "data platform" in job.description
        assert "Spark" in job.description

    def test_epoch_millis_converted(self):
        job = LeverSource(board_token="acme")._parse(self._job())
        assert job.posted_at is not None
        assert job.posted_at.year == 2025

    def test_workplace_type_sets_remote_flag(self):
        src = LeverSource(board_token="acme")
        assert src._parse(self._job()).remote_flag is True
        assert src._parse(self._job(workplaceType="onsite")).remote_flag is None

    def test_missing_fields_do_not_raise(self):
        job = LeverSource(board_token="acme")._parse({"id": "x"})
        assert job.title == "" and job.posted_at is None


class TestAshby:
    def _job(self, **over):
        base = {
            "id": "job-1",
            "title": "ML Engineer",
            "location": "Remote",
            "jobUrl": "https://jobs.ashbyhq.com/acme/job-1",
            "descriptionPlain": "Train models.",
            "publishedAt": "2026-08-05T00:00:00Z",
            "isListed": True,
            "isRemote": True,
            "companyName": "Acme",
        }
        return base | over

    def test_parses_a_normal_posting(self):
        job = AshbySource(board_token="acme")._parse(self._job())
        assert job.title == "ML Engineer"
        assert job.company == "Acme"
        assert job.remote_flag is True

    def test_structured_compensation_extracted(self):
        item = self._job(compensation={"compensationTiers": [{"components": [{
            "compensationType": "Salary", "interval": "1 YEAR",
            "minValue": 120000, "maxValue": 160000, "currencyCode": "USD",
        }]}]})
        lo, hi, cur = _compensation(item)
        assert (lo, hi, cur) == (120000, 160000, "USD")

    def test_equity_component_ignored(self):
        item = self._job(compensation={"compensationTiers": [{"components": [{
            "compensationType": "Equity", "minValue": 1, "maxValue": 2,
        }]}]})
        assert _compensation(item) == (None, None, "")

    def test_hourly_rate_ignored(self):
        item = self._job(compensation={"compensationTiers": [{"components": [{
            "compensationType": "Salary", "interval": "1 HOUR",
            "minValue": 60, "maxValue": 90,
        }]}]})
        assert _compensation(item) == (None, None, "")

    def test_missing_compensation_is_safe(self):
        assert _compensation({}) == (None, None, "")
        assert _compensation({"compensation": "nonsense"}) == (None, None, "")


class TestRemoteOk:
    def _job(self, **over):
        base = {
            "id": "99",
            "position": "Python Developer",
            "company": "Globex",
            "location": "Worldwide",
            "description": "<p>Remote Python role.</p>",
            "apply_url": "https://remoteok.com/l/99",
            "date": "2026-08-06T00:00:00Z",
            "tags": ["python", "backend"],
            "salary_min": 90000,
            "salary_max": 130000,
        }
        return base | over

    def test_parses_a_normal_posting(self):
        job = RemoteOkSource()._parse(self._job())
        assert job.title == "Python Developer"
        assert job.company == "Globex"
        assert job.remote_flag is True
        assert job.salary_is_estimate is True, "aggregator salary must not drive hard filters"

    def test_search_term_filter(self):
        src = RemoteOkSource(search_terms=["python"])
        assert src._matches(src._parse(self._job())) is True
        assert src._matches(src._parse(self._job(position="Chef", tags=[]))) is False

    def test_no_search_terms_matches_everything(self):
        src = RemoteOkSource()
        assert src._matches(src._parse(self._job(position="Chef", tags=[]))) is True

    def test_int_coercion(self):
        assert _int_or_none(90000) == 90000
        assert _int_or_none("$90,000") == 90000
        assert _int_or_none(0) is None
        assert _int_or_none(None) is None
        assert _int_or_none(True) is None
