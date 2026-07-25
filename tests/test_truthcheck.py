"""Truthcheck tests.

The critical assertions are that fabricated content is *blocked*. A false pass
here means an invented claim reaches a real employer, so these cases are the
sharpest tests in the suite.
"""

from __future__ import annotations

from app.pipeline.truthcheck import verify

RESUME = """
Jane Doe
Software Engineer

Experience
Acme Corp - Backend Engineer, Mar 2022 - Present
- Built REST APIs in Python using FastAPI and PostgreSQL
- Reduced p95 latency by 40% by adding Redis caching
- Deployed services with Docker on AWS

Education
BSc Computer Science, University of Nairobi, 2021

Skills
Python, FastAPI, PostgreSQL, Redis, Docker, AWS, Git
"""

GITHUB = """
Repository: task-queue (Python)
  A distributed task queue built with Python, Redis and Celery.
Repository: web-scraper (Python)
  Async scraper using httpx and BeautifulSoup.
"""


def bullet(rewritten, original="", evidence="resume"):
    return {"bullets": [{
        "section": "Experience", "original": original,
        "rewritten": rewritten, "evidence": evidence,
    }]}


class TestBlocksFabrication:
    def test_invented_metric_blocked(self):
        r = verify(bullet("Reduced latency by 90% across all services"),
                   resume_text=RESUME, github_evidence=GITHUB)
        assert not r.passed
        assert any(v.kind == "invented_metric" for v in r.blocks)

    def test_invented_team_size_blocked(self):
        r = verify(bullet("Led a team of 12 engineers"),
                   resume_text=RESUME, github_evidence=GITHUB)
        assert not r.passed
        assert any(v.kind == "invented_metric" for v in r.blocks)

    def test_invented_degree_blocked(self):
        r = verify(bullet("Completed an MBA while working full time"),
                   resume_text=RESUME, github_evidence=GITHUB)
        assert not r.passed
        assert any(v.kind == "invented_credential" for v in r.blocks)

    def test_unsupported_skill_blocked(self):
        # Kubernetes appears nowhere in the resume or GitHub.
        r = verify(bullet("Orchestrated deployments with Kubernetes"),
                   resume_text=RESUME, github_evidence=GITHUB)
        assert not r.passed
        assert any(v.kind == "unsupported_skill" for v in r.blocks)

    def test_unsupported_skill_in_skills_list_blocked(self):
        r = verify({"bullets": [], "skills_to_surface": ["Kubernetes", "Terraform"]},
                   resume_text=RESUME, github_evidence=GITHUB)
        assert not r.passed

    def test_invented_metric_in_summary_blocked(self):
        r = verify({"bullets": [], "summary": "Engineer who cut costs by 75%."},
                   resume_text=RESUME, github_evidence=GITHUB)
        assert not r.passed

    def test_multiple_fabrications_all_reported(self):
        r = verify(bullet("Led 30 engineers and improved throughput by 500% using Kubernetes"),
                   resume_text=RESUME, github_evidence=GITHUB)
        assert len(r.blocks) >= 2


class TestAllowsTruthfulRewrites:
    def test_faithful_rephrase_passes(self):
        r = verify(
            bullet("Built and shipped REST APIs in Python with FastAPI and PostgreSQL",
                   original="Built REST APIs in Python using FastAPI and PostgreSQL"),
            resume_text=RESUME, github_evidence=GITHUB)
        assert r.passed, [str(v) for v in r.blocks]

    def test_real_metric_preserved(self):
        r = verify(
            bullet("Cut p95 latency 40% by introducing Redis caching",
                   original="Reduced p95 latency by 40% by adding Redis caching"),
            resume_text=RESUME, github_evidence=GITHUB)
        assert r.passed, [str(v) for v in r.blocks]

    def test_skill_from_github_allowed(self):
        # Celery is absent from the resume but proven by a real repository.
        r = verify(bullet("Built a distributed task queue with Celery and Redis",
                          evidence="task-queue repo"),
                   resume_text=RESUME, github_evidence=GITHUB)
        assert r.passed, [str(v) for v in r.blocks]

    def test_year_is_not_treated_as_invented_metric(self):
        r = verify(bullet("Backend Engineer at Acme Corp since 2022",
                          original="Acme Corp - Backend Engineer, Mar 2022 - Present"),
                   resume_text=RESUME, github_evidence=GITHUB)
        assert r.passed, [str(v) for v in r.blocks]

    def test_empty_output_passes_trivially(self):
        r = verify({"bullets": []}, resume_text=RESUME)
        assert r.passed


class TestFlagsWithoutBlocking:
    def test_scope_inflation_is_flagged_not_blocked(self):
        r = verify(bullet("Led development of REST APIs in Python",
                          original="Built REST APIs in Python using FastAPI"),
                   resume_text=RESUME, github_evidence=GITHUB)
        assert any(v.kind == "scope_inflation" for v in r.flags)

    def test_missing_evidence_flagged(self):
        r = verify(bullet("Built REST APIs in Python", evidence=""),
                   resume_text=RESUME, github_evidence=GITHUB)
        assert any(v.kind == "missing_evidence" for v in r.flags)

    def test_unknown_company_flagged(self):
        r = verify(bullet("Built REST APIs in Python at Globex"),
                   resume_text=RESUME, github_evidence=GITHUB)
        assert any(v.kind == "unknown_proper_noun" for v in r.flags)


class TestRobustness:
    def test_malformed_payload_does_not_crash(self):
        for payload in ({}, {"bullets": "nope"}, {"bullets": [None, 42]}):
            assert verify(payload, resume_text=RESUME) is not None

    def test_checker_failure_never_reports_pass(self):
        # A crash inside verification must not be treated as approval. This is
        # the fail-safe direction: unknown means blocked, never allowed.
        class Hostile(dict):
            def get(self, *_a, **_kw):
                raise RuntimeError("boom")

        report = verify(Hostile(), resume_text=RESUME)
        assert report.passed is False
        assert report.checker_failed is True
        assert any(v.kind == "checker_error" for v in report.blocks)

    def test_empty_source_blocks_claims(self):
        r = verify(bullet("Built REST APIs in Python"), resume_text="")
        assert not r.passed
