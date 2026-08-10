"""Tests for the Adzuna API connector.

No network. Feeds a trimmed real-API payload straight to the parser.
"""

from __future__ import annotations

from app.config import Settings
from app.sources.adzuna import AdzunaSource, _adzuna_settings
from app.sources.base import RawJob

ADZUNA_PAYLOAD = {
    "results": [
        {
            "id": "123456",
            "title": "Senior Python Engineer",
            "company": {"display_name": "Acme Corp"},
            "location": {"display_name": "San Francisco, CA, US"},
            "description": "<p>Build <strong>Python</strong> services.</p>",
            "redirect_url": "https://adzuna.com/jobs/123456",
            "created": "2026-08-07T10:30:00Z",
            "salary_min": 120000,
            "salary_max": 160000,
            "salary_is_predicted": True,
            "category": {"label": "IT Jobs"},
            "contract_type": "permanent",
            "contract_time": "full_time",
        },
        {
            "id": "789012",
            "title": "Frontend Developer",
            "company": {"display_name": "Startup Inc"},
            "location": {"display_name": "Remote"},
            "description": "React + TypeScript role.",
            "redirect_url": "https://adzuna.com/jobs/789012",
            "created": "2026-08-06T14:00:00Z",
            "salary_min": None,
            "salary_max": None,
            "salary_is_predicted": False,
            "category": {"label": "IT Jobs"},
            "contract_type": "contract",
            "contract_time": "part_time",
        },
    ]
}


def test_adzuna_settings_when_configured() -> None:
    s = Settings(adzuna_app_id="test_id", adzuna_app_key="test_key")
    assert _adzuna_settings(s) == ("test_id", "test_key")


def test_adzuna_settings_when_missing() -> None:
    s = Settings(adzuna_app_id="", adzuna_app_key="")
    assert _adzuna_settings(s) is None
    s2 = Settings(adzuna_app_id="only_id", adzuna_app_key="")
    assert _adzuna_settings(s2) is None


def test_adzuna_parse_single_posting() -> None:
    src = AdzunaSource(search_terms=["python"])
    job = src._parse(ADZUNA_PAYLOAD["results"][0])
    assert job.source == "adzuna"
    assert job.source_id == "123456"
    assert job.title == "Senior Python Engineer"
    assert job.company == "Acme Corp"
    assert job.location == "San Francisco, CA, US"
    assert "Build Python services" in job.description
    assert "<p>" not in job.description
    assert job.apply_url == "https://adzuna.com/jobs/123456"
    assert job.posted_at is not None and job.posted_at.year == 2026
    assert job.salary_min == 120000
    assert job.salary_max == 160000
    assert job.salary_is_estimate is True
    assert job.extra["category"] == "IT Jobs"
    assert job.extra["contract_type"] == "permanent"
    assert job.extra["contract_time"] == "full_time"


def test_adzuna_parse_handles_missing_salary() -> None:
    src = AdzunaSource()
    job = src._parse(ADZUNA_PAYLOAD["results"][1])
    assert job.salary_min is None
    assert job.salary_max is None
    assert job.salary_is_estimate is False


def test_adzuna_matches_by_search_terms() -> None:
    src = AdzunaSource(search_terms=["python", "react"])
    matched = RawJob(source="adzuna", source_id="1", company="x", title="Python Engineer",
                     description="python and django", extra={"category": "IT"})
    assert src._matches(matched) is True
    unmatched = RawJob(source="adzuna", source_id="1", company="x", title="Sales Manager",
                       description="sales", extra={"category": "Sales"})
    assert src._matches(unmatched) is False


def test_adzuna_matches_when_no_terms() -> None:
    src = AdzunaSource()
    assert src._matches(RawJob(source="adzuna", source_id="1", company="x", title="Anything")) is True
