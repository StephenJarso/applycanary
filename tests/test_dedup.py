"""Dedup resolver tests against a real in-memory database."""

from __future__ import annotations

import pytest
from sqlmodel import Session, SQLModel, create_engine, select

from app.models import Job, JobAlias
from app.pipeline.dedup import resolve, titles_match
from app.pipeline.normalize import canonical_url, fingerprint


@pytest.fixture()
def session():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(engine)
    with Session(engine) as s:
        yield s


def make_job(company="Acme", title="Backend Engineer", location="Remote",
             source="greenhouse", source_id="1", url="https://acme.com/j/1",
             description="", **kw) -> Job:
    return Job(
        fingerprint=fingerprint(company, title, location),
        source=source, source_id=source_id, company=company, title=title,
        location=location, apply_url=url, canonical_url=canonical_url(url),
        description=description, **kw,
    )


class TestTitlesMatch:
    def test_identical_after_normalisation(self):
        ok, score = titles_match("Backend Engineer (m/f/d)", "backend engineer")
        assert ok and score == 100.0

    def test_word_order_variation_matches(self):
        ok, _ = titles_match("Senior Backend Engineer", "Backend Engineer, Senior")
        assert ok

    def test_scope_change_blocked(self):
        # The false positive a bare fuzzy ratio would wave through.
        assert titles_match("Backend Engineer", "Backend Engineering Manager")[0] is False
        assert titles_match("Engineer", "Engineering Lead")[0] is False

    def test_seniority_mismatch_blocked(self):
        assert titles_match("Senior Data Analyst", "Data Analyst")[0] is False
        assert titles_match("Intern Developer", "Developer")[0] is False

    def test_level_numeral_mismatch_blocked(self):
        assert titles_match("Engineer II", "Engineer III")[0] is False

    def test_unrelated_titles_do_not_match(self):
        assert titles_match("Backend Engineer", "Graphic Designer")[0] is False

    def test_empty_is_no_match(self):
        assert titles_match("", "Backend Engineer")[0] is False


class TestResolve:
    def test_first_sighting_is_new(self, session):
        result = resolve(session, make_job())
        session.commit()
        assert result.is_new is True
        assert result.matched_by == ""
        assert len(session.exec(select(Job)).all()) == 1

    def test_layer1_fingerprint_dedups(self, session):
        session.add(make_job())
        session.commit()
        result = resolve(session, make_job(company="Acme Inc.", title="Backend Engineer (m/f/d)",
                                          location="Anywhere", source="remoteok", source_id="9",
                                          url="https://remoteok.com/x"))
        session.commit()
        assert result.is_new is False
        assert result.matched_by == "fingerprint"
        assert len(session.exec(select(Job)).all()) == 1

    def test_layer2_canonical_url_dedups(self, session):
        session.add(make_job(url="https://acme.com/j/1"))
        session.commit()
        # Different title wording so fingerprint misses, but same posting URL.
        result = resolve(session, make_job(
            title="Backend Developer", source="linkedin", source_id="7",
            url="https://www.acme.com/j/1/?utm_source=linkedin"))
        session.commit()
        assert result.is_new is False
        assert result.matched_by == "canonical_url"

    def test_layer3_fuzzy_title_dedups(self, session):
        session.add(make_job(title="Senior Backend Engineer"))
        session.commit()
        result = resolve(session, make_job(
            title="Backend Engineer, Senior", source="lever", source_id="4",
            url="https://jobs.lever.co/acme/999"))
        session.commit()
        assert result.is_new is False
        assert result.matched_by == "fuzzy_title"

    def test_distinct_jobs_both_stored(self, session):
        session.add(make_job(title="Backend Engineer"))
        session.commit()
        result = resolve(session, make_job(
            title="Frontend Engineer", source="lever", source_id="2",
            url="https://jobs.lever.co/acme/2"))
        session.commit()
        assert result.is_new is True
        assert len(session.exec(select(Job)).all()) == 2

    def test_same_title_different_company_both_stored(self, session):
        session.add(make_job(company="Acme"))
        session.commit()
        result = resolve(session, make_job(
            company="Globex", source="lever", source_id="3",
            url="https://jobs.lever.co/globex/3"))
        session.commit()
        assert result.is_new is True

    def test_duplicate_recorded_as_alias(self, session):
        session.add(make_job())
        session.commit()
        resolve(session, make_job(source="remoteok", source_id="55",
                                  url="https://remoteok.com/55"))
        session.commit()
        aliases = session.exec(select(JobAlias)).all()
        assert len(aliases) == 1
        assert aliases[0].source == "remoteok"

    def test_alias_not_duplicated_on_repeat_poll(self, session):
        session.add(make_job())
        session.commit()
        for _ in range(3):
            resolve(session, make_job(source="remoteok", source_id="55",
                                      url="https://remoteok.com/55"))
            session.commit()
        assert len(session.exec(select(JobAlias)).all()) == 1

    def test_seen_count_increments(self, session):
        session.add(make_job())
        session.commit()
        result = resolve(session, make_job(source="remoteok", source_id="55",
                                           url="https://remoteok.com/55"))
        session.commit()
        assert result.job.seen_count == 2

    def test_richer_description_wins(self, session):
        session.add(make_job(description="short"))
        session.commit()
        long_desc = "a much fuller description " * 20
        result = resolve(session, make_job(source="remoteok", source_id="55",
                                           url="https://remoteok.com/55",
                                           description=long_desc))
        session.commit()
        assert result.job.description == long_desc

    def test_shorter_description_does_not_overwrite(self, session):
        long_desc = "a much fuller description " * 20
        session.add(make_job(description=long_desc))
        session.commit()
        result = resolve(session, make_job(source="remoteok", source_id="55",
                                           url="https://remoteok.com/55",
                                           description="short"))
        session.commit()
        assert result.job.description == long_desc

    def test_salary_backfilled_from_duplicate(self, session):
        session.add(make_job())
        session.commit()
        result = resolve(session, make_job(source="remoteok", source_id="55",
                                           url="https://remoteok.com/55",
                                           salary_min=90000, salary_max=120000,
                                           salary_currency="USD"))
        session.commit()
        assert result.job.salary_min == 90000

    def test_ats_platform_preserved_from_duplicate(self, session):
        # The sanctioned-API platform enables auto-submit and must never be lost.
        session.add(make_job())
        session.commit()
        result = resolve(session, make_job(
            source="smartrecruiters", source_id="55",
            url="https://jobs.smartrecruiters.com/acme/55",
            ats_platform="smartrecruiters", ats_board_token="acme"))
        session.commit()
        assert result.job.ats_platform == "smartrecruiters"
        assert result.job.ats_board_token == "acme"
