"""Parsing tests for the aggregator and ATS connectors added after the initial five.

Fixtures are trimmed copies of real payloads captured from each live endpoint,
so a shape change upstream shows up here rather than as an empty poll in
production. No network access.
"""

from __future__ import annotations

from app.sources.arbeitnow import ArbeitnowSource
from app.sources.base import parse_epoch
from app.sources.himalayas import HimalayasSource
from app.sources.jobicy import JobicySource
from app.sources.remotive import RemotiveSource
from app.sources.workable import WorkableSource


class TestParseEpoch:
    def test_seconds(self):
        dt = parse_epoch(1786136428)
        assert dt is not None and dt.year == 2026 and dt.tzinfo is None

    def test_milliseconds(self):
        dt = parse_epoch(1786136428000, unit="ms")
        assert dt is not None and dt.year == 2026

    def test_rejects_out_of_range(self):
        # 0 and far-future placeholders would otherwise pin jobs to the top or
        # bottom of every sort forever.
        assert parse_epoch(0) is None
        assert parse_epoch(99_999_999_999) is None

    def test_rejects_non_numeric(self):
        for bad in (None, "", "2026", [], True):
            assert parse_epoch(bad) is None


class TestRemotive:
    def _job(self, **over):
        return {
            "id": 2091088,
            "title": "Senior Python Engineer",
            "company_name": "Creative Force ",
            "candidate_required_location": "Europe",
            "job_type": "full_time",
            "salary": "$70k - $90k",
            "publication_date": "2026-08-05T09:45:42",
            "url": "https://remotive.com/remote-jobs/eng/senior-python-2091088",
            "description": "<p>Build <strong>Python</strong> services.</p>",
            "tags": ["python", "django"],
            "category": "Software Development",
        } | over

    def test_parses_a_posting(self):
        job = RemotiveSource()._parse(self._job())
        assert job.source == "remotive"
        assert job.source_id == "2091088"
        assert job.company == "Creative Force"
        assert job.title == "Senior Python Engineer"
        assert "Python" in job.description and "<p>" not in job.description
        assert job.posted_at is not None and job.posted_at.year == 2026

    def test_always_remote(self):
        # candidate_required_location is eligibility, not an office.
        job = RemotiveSource()._parse(self._job(candidate_required_location="USA Only"))
        assert job.remote_flag is True
        assert job.to_job().is_remote is True

    def test_free_text_salary_is_not_parsed(self):
        job = RemotiveSource()._parse(self._job())
        assert job.salary_min is None and job.salary_max is None
        assert job.extra["salary_note"] == "$70k - $90k"

    def test_search_terms_filter(self):
        src = RemotiveSource(search_terms=["python"])
        assert src._matches(src._parse(self._job())) is True
        assert src._matches(src._parse(self._job(title="Chef", tags=[]))) is False

    def test_missing_fields_do_not_raise(self):
        job = RemotiveSource()._parse({"id": 1})
        assert job.title == "" and job.posted_at is None


class TestArbeitnow:
    def _job(self, **over):
        return {
            "slug": "backend-engineer-berlin-229061",
            "title": "Backend Engineer",
            "company_name": "People Places",
            "location": "Berlin",
            "remote": True,
            "job_types": ["Full Time"],
            "tags": ["Remote", "Engineering"],
            "created_at": 1786136428,
            "url": "https://www.arbeitnow.com/jobs/companies/pp/backend-engineer",
            "description": "<p>Python and Django.</p>",
        } | over

    def test_parses_a_posting(self):
        job = ArbeitnowSource()._parse(self._job())
        assert job.source_id == "backend-engineer-berlin-229061"
        assert job.company == "People Places"
        assert job.location == "Berlin"
        assert job.posted_at is not None and job.posted_at.year == 2026

    def test_uses_explicit_remote_flag(self):
        assert ArbeitnowSource()._parse(self._job(remote=True)).remote_flag is True
        assert ArbeitnowSource()._parse(self._job(remote=False)).remote_flag is False

    def test_absent_remote_key_is_unknown(self):
        item = self._job()
        del item["remote"]
        assert ArbeitnowSource()._parse(item).remote_flag is None

    def test_remote_only_option_filters(self):
        src = ArbeitnowSource(remote_only=True)
        assert src.remote_only is True
        assert src._parse(self._job(remote=False)).remote_flag is False


class TestJobicy:
    def _job(self, **over):
        return {
            "id": 144804,
            "jobTitle": "Data Engineer",
            "companyName": "Sanofi",
            "jobGeo": "USA",
            "jobType": ["Full-Time"],
            "jobLevel": "Senior",
            "jobIndustry": ["Data Science"],
            "annualSalaryMin": 120000,
            "annualSalaryMax": 160000,
            "salaryCurrency": "USD",
            "pubDate": "2026-08-07T16:45:04+00:00",
            "url": "https://jobicy.com/jobs/144804-data-engineer",
            "jobDescription": "<p>SQL and Python.</p>",
        } | over

    def test_parses_a_posting(self):
        job = JobicySource()._parse(self._job())
        assert job.source_id == "144804"
        assert job.company == "Sanofi"
        assert job.title == "Data Engineer"
        assert job.remote_flag is True

    def test_structured_salary_is_kept_but_marked_estimate(self):
        job = JobicySource()._parse(self._job())
        assert (job.salary_min, job.salary_max) == (120000, 160000)
        assert job.salary_currency == "USD"
        assert job.salary_is_estimate is True

    def test_null_salary_clears_currency(self):
        job = JobicySource()._parse(self._job(annualSalaryMin=None, annualSalaryMax=None))
        assert job.salary_min is None and job.salary_max is None
        # Currency without a figure is noise in the UI.
        assert job.salary_currency == ""

    def test_zero_salary_treated_as_absent(self):
        job = JobicySource()._parse(self._job(annualSalaryMin=0, annualSalaryMax=0))
        assert job.salary_min is None and job.salary_max is None

    def test_falls_back_to_excerpt(self):
        item = self._job(jobDescription="")
        item["jobExcerpt"] = "Short summary"
        assert "Short summary" in JobicySource()._parse(item).description


class TestHimalayas:
    def _job(self, **over):
        return {
            "title": "Product Manager",
            "companyName": "Hone Health",
            "locationRestrictions": ["United States"],
            "employmentType": "Full Time",
            "minSalary": 130000,
            "maxSalary": 160000,
            "currency": "USD",
            "pubDate": 1786148790,
            "applicationLink": "https://himalayas.app/companies/hone/jobs/pm",
            "guid": "https://himalayas.app/companies/hone/jobs/pm",
            "description": "<p>Own the roadmap.</p>",
            "categories": ["Product"],
            "companySlug": "hone-health",
        } | over

    def test_parses_a_posting(self):
        job = HimalayasSource()._parse(self._job())
        assert job.company == "Hone Health"
        assert job.location == "United States"
        assert (job.salary_min, job.salary_max) == (130000, 160000)
        assert job.posted_at is not None and job.posted_at.year == 2026

    def test_guid_is_the_identifier(self):
        # There is no numeric id on this API.
        job = HimalayasSource()._parse(self._job())
        assert job.source_id == "https://himalayas.app/companies/hone/jobs/pm"

    def test_empty_restrictions_means_worldwide(self):
        assert HimalayasSource()._parse(self._job(locationRestrictions=[])).location == "Worldwide"

    def test_multiple_restrictions_joined(self):
        job = HimalayasSource()._parse(self._job(locationRestrictions=["United States", "Canada"]))
        assert job.location == "United States, Canada"

    def test_limit_is_capped(self):
        # The API rejects limits above 50.
        assert HimalayasSource(limit=500).limit == 50


class TestWorkable:
    def _job(self, **over):
        return {
            "shortcode": "B76C11B977",
            "title": "Analytics Engineer",
            "employment_type": "Full-time",
            "telecommuting": False,
            "department": "Business Intelligence",
            "url": "https://apply.workable.com/j/B76C11B977",
            "application_url": "https://apply.workable.com/j/B76C11B977/apply",
            "published_on": "2026-07-10",
            "country": "United Kingdom",
            "city": "London",
            "state": "England",
            "description": "<p>dbt and SQL.</p>",
            "experience": "Mid-Senior level",
            "industry": "IT",
        } | over

    def test_parses_a_posting(self):
        job = WorkableSource(board_token="zego")._parse(self._job(), "Zego")
        assert job.source_id == "B76C11B977"
        assert job.company == "Zego"
        assert job.location == "London, England, United Kingdom"
        assert job.ats_platform == "workable"
        assert job.ats_board_token == "zego"

    def test_prefers_application_url(self):
        # application_url lands on the form; url is only the listing page.
        job = WorkableSource(board_token="zego")._parse(self._job(), "Zego")
        assert job.apply_url.endswith("/apply")

    def test_telecommuting_maps_to_remote(self):
        src = WorkableSource(board_token="zego")
        assert src._parse(self._job(telecommuting=True), "Zego").remote_flag is True
        assert src._parse(self._job(telecommuting=False), "Zego").remote_flag is False

    def test_partial_location_omits_blanks(self):
        job = WorkableSource(board_token="zego")._parse(
            self._job(city="", state=""), "Zego"
        )
        assert job.location == "United Kingdom"

    def test_board_token_is_required(self):
        try:
            WorkableSource(board_token="")
        except ValueError:
            return
        raise AssertionError("expected ValueError for empty board_token")
