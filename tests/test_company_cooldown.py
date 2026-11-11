"""Company cooldowns: acting on "apply again in 9-12 months" without a ban.

`excluded_companies` is permanent, so a rejection that asks the candidate to come
back next year had no way to be honoured. A cooldown carries an expiry, is
enforced by the same tier-1 filter, and lifts on its own.
"""

from __future__ import annotations

from datetime import date, timedelta

from sqlmodel import Session, select

from app.api.router import (
    CooldownClearIn,
    CooldownIn,
    clear_company_cooldown,
    set_company_cooldown,
)
from app.db import engine, init_db
from app.models import Job, Profile, User
from app.pipeline.score import active_cooldown, hard_disqualifier


def _profile(**kwargs: object) -> Profile:
    return Profile(user_id=1, **kwargs)  # type: ignore[arg-type]


def _job(company: str = "HENNGE") -> Job:
    return Job(
        fingerprint="fp-cooldown",
        source="test",
        company=company,
        title="Global Internship Program",
        apply_url="https://example.test/apply",
    )


class TestActiveCooldown:
    def test_future_date_is_active(self):
        until = (date.today() + timedelta(days=30)).isoformat()
        profile = _profile(company_cooldowns={"hennge": until})
        assert active_cooldown(profile, "HENNGE") == until

    def test_expired_date_is_inactive(self):
        until = (date.today() - timedelta(days=1)).isoformat()
        profile = _profile(company_cooldowns={"hennge": until})
        assert active_cooldown(profile, "HENNGE") == ""

    def test_malformed_date_does_not_hide_the_company(self):
        profile = _profile(company_cooldowns={"hennge": "soon"})
        assert active_cooldown(profile, "HENNGE") == ""

    def test_company_matching_ignores_case_and_space(self):
        until = (date.today() + timedelta(days=5)).isoformat()
        profile = _profile(company_cooldowns={"hennge": until})
        assert active_cooldown(profile, "  Hennge ") == until

    def test_cooldown_disqualifies_the_job_and_quotes_the_date(self):
        until = (date.today() + timedelta(days=365)).isoformat()
        reason = hard_disqualifier(
            _job(), _profile(company_cooldowns={"hennge": until})
        )
        assert "cooling down" in reason
        assert until in reason

    def test_an_exclusion_is_still_reported_first(self):
        until = (date.today() + timedelta(days=365)).isoformat()
        reason = hard_disqualifier(
            _job(),
            _profile(
                excluded_companies=["HENNGE"], company_cooldowns={"hennge": until}
            ),
        )
        assert "excluded list" in reason


class TestEndpoint:
    async def test_setting_then_clearing_a_cooldown(self):
        init_db()
        with Session(engine) as session:
            user = User(email="cooldown@example.test", password_hash="x")
            session.add(user)
            session.commit()
            session.refresh(user)

            profile = Profile(user_id=user.id, base_resume_text="Python")
            job = Job(
                fingerprint="fp-cooldown-endpoint",
                source="test",
                company="HENNGE",
                title="Global Internship Program",
                apply_url="https://example.test/apply",
                description="Python. Keep your letter under 200 words.",
            )
            session.add(profile)
            session.add(job)
            session.commit()
            session.refresh(profile)
            session.refresh(job)
            try:
                result = await set_company_cooldown(
                    job.id or 0, CooldownIn(months=12), session, user
                )
                assert result.ok
                session.refresh(profile)
                assert "hennge" in profile.company_cooldowns
                assert result.detail["until"]

                reason = hard_disqualifier(_job(), profile)
                assert "cooling down" in reason

                cleared = await clear_company_cooldown(
                    CooldownClearIn(company="HENNGE"), session, user
                )
                assert cleared.ok
                session.refresh(profile)
                assert profile.company_cooldowns == {}
                assert "cooling down" not in hard_disqualifier(_job(), profile)
            finally:
                for row in session.exec(
                    select(Profile).where(Profile.user_id == user.id)
                ).all():
                    session.delete(row)
                session.delete(job)
                session.delete(user)
                session.commit()
