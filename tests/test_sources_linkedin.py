"""Pure parser tests for the LinkedIn guest connector.

No network: markup is the contract, so a LinkedIn HTML change shows up here as
a failing fixture instead of a silently empty poll.
"""

from __future__ import annotations

from app.sources import all_sources
from app.sources.linkedin import (
    _parse_applicants,
    _parse_cards,
    _parse_description,
    _parse_salary,
)

CARD_HTML = """
<ul>
  <li>
    <div class="base-card job-search-card">
      <a class="base-card__full-link" href="https://www.linkedin.com/jobs/view/software-engineer-at-acme-4123456789?refId=abc">
        <span class="sr-only">Software Engineer</span>
      </a>
      <div class="base-search-card__info">
        <h3 class="base-search-card__title">Software Engineer</h3>
        <h4 class="base-search-card__subtitle"><a href="/company/acme">Acme</a></h4>
        <div class="base-search-card__metadata">
          <span class="job-search-card__location">Nairobi, Kenya</span>
          <time class="job-search-card__listdate" datetime="2026-10-19">2 days ago</time>
          <span class="job-search-card__salary-info">$120,000 - $160,000/yr</span>
        </div>
      </div>
    </div>
  </li>
  <li>
    <div class="base-card job-search-card">
      <a class="base-card__full-link" href="https://www.linkedin.com/jobs/view/golang-intern-at-beta-4987654321">
        <span class="sr-only">Golang Intern</span>
      </a>
      <div class="base-search-card__info">
        <h3 class="base-search-card__title">Golang Intern</h3>
        <h4 class="base-search-card__subtitle">Beta Labs</h4>
        <span class="job-search-card__location">Remote</span>
      </div>
    </div>
  </li>
</ul>
"""

DETAIL_HTML = """
<section class="description">
  <div class="description__text description__text--rich">
    <div class="show-more-less-html__markup">
      <p>Build Go services. Health insurance and 401(k) included.</p>
    </div>
  </div>
  <span class="num-applicants__caption">27 applicants</span>
</section>
"""


def test_linkedin_source_is_registered():
    assert "linkedin" in all_sources()


def test_parse_cards_extracts_public_fields():
    jobs = _parse_cards(CARD_HTML)
    assert len(jobs) == 2
    first = jobs[0]
    assert first.title == "Software Engineer"
    assert first.company == "Acme"
    assert first.location == "Nairobi, Kenya"
    assert first.apply_url == (
        "https://www.linkedin.com/jobs/view/software-engineer-at-acme-4123456789"
    )
    assert first.source_id == "4123456789"
    assert first.salary_min == 120_000
    assert first.salary_max == 160_000
    assert first.salary_currency == "USD"
    assert first.posted_at is not None
    assert jobs[1].title == "Golang Intern"
    assert jobs[1].salary_min is None


def test_parse_detail_gets_description_and_applicants():
    description = _parse_description(DETAIL_HTML)
    assert "Build Go services" in description
    assert _parse_applicants(DETAIL_HTML) == 27
    # No applicant line must remain unknown, never zero.
    assert _parse_applicants("<p>Be an early applicant</p>") is None


def test_parse_salary_variants():
    assert _parse_salary("£70k - £90k") == (70_000, 90_000, "GBP")
    assert _parse_salary("€80,000") == (80_000, 80_000, "EUR")
    assert _parse_salary("Competitive") == (None, None, "")
