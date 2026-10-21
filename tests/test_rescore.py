"""Re-scoring existing decisions applies new filters retroactively.

A relevance fix only helps postings discovered afterwards unless stored scores
are recomputed, so this covers the sweep that re-evaluates a user's existing
matches (highest score first) and flips now-invalid roles to rejected.
"""

from __future__ import annotations

import asyncio

import pytest
from sqlmodel import Session, select

from app.db import engine, init_db
from app.models import Job, JobScore, JobStatus, Profile, User, UserJob, utcnow
from app.pipeline.score import rescore_scored


def asyncio_run(coro):  # noqa: ANN001, ANN201
    # Private loop: asyncio.run() clears the thread's event loop, which breaks
    # modules that use get_event_loop() later in the same session.
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


@pytest.fixture
def isolated_db():
    """Start from empty user/job tables so one test cannot see another's rows."""
    init_db()
    with Session(engine) as session:
        for row in session.exec(select(JobScore)).all():
            session.delete(row)
        for row in session.exec(select(UserJob)).all():
            session.delete(row)
        for row in session.exec(select(Profile)).all():
            session.delete(row)
        for row in session.exec(select(Job)).all():
            session.delete(row)
        for row in session.exec(select(User)).all():
            session.delete(row)
        session.commit()
    yield


def _seed(
    *, title: str, location: str, remote: bool, fingerprint: str,
) -> tuple[int, int]:
    """A user whose profile says backend, plus one stale top-scored match."""
    with Session(engine) as session:
        user = User(email="rescore@applycanary.test", password_hash="dummy")
        session.add(user)
        session.commit()
        session.refresh(user)
        profile = Profile(
            user_id=user.id,
            email=user.email,
            location="Nairobi, Kenya",
            target_titles=["Backend Engineer"],
            target_locations=["Nairobi", "Kenya", "Remote"],
            years_experience=2,
            base_resume_text="golang postgres docker kubernetes",
            skills=["go", "postgresql"],
        )
        job = Job(
            title=title,
            company="Acme",
            source="test",
            location=location,
            is_remote=remote,
            description="Own positioning and messaging for our product launches.",
            fingerprint=fingerprint,
            posted_at=utcnow(),
            first_seen_at=utcnow(),
        )
        session.add_all([profile, job])
        session.commit()
        session.refresh(profile)
        session.refresh(job)
        # The stale decision the dashboard currently ranks at the top.
        session.add(
            JobScore(
                job_id=job.id,
                user_id=user.id,
                total=100.0,
                verdict="possible",
                decided_by="tier1_keyword",
                keyword_score=100.0,
                scored_at=utcnow(),
            )
        )
        session.add(UserJob(user_id=user.id, job_id=job.id, status=JobStatus.SCORED))
        session.commit()
        return user.id, job.id


def _rescore(user_id: int) -> int:
    with Session(engine) as session:
        return asyncio_run(rescore_scored(session, user_id=user_id, limit=10))


def _score_and_state(user_id: int, job_id: int):  # noqa: ANN202
    with Session(engine) as session:
        score = session.exec(
            select(JobScore).where(
                JobScore.user_id == user_id, JobScore.job_id == job_id
            )
        ).one()
        state = session.exec(
            select(UserJob).where(
                UserJob.user_id == user_id, UserJob.job_id == job_id
            )
        ).one()
    return score, state


def test_rescore_rejects_a_non_engineering_role(isolated_db):
    user_id, job_id = _seed(
        title="Product Marketing Manager, Market Intelligence",
        location="Nairobi, Kenya",
        remote=False,
        fingerprint="rescore-marketing",
    )

    assert _rescore(user_id) == 1

    score, state = _score_and_state(user_id, job_id)
    assert score.verdict == "disqualified"
    assert score.total == 0.0
    assert "not a software engineering position" in score.disqualifier
    assert state.status == JobStatus.REJECTED


def test_rescore_rejects_a_remote_role_limited_abroad(isolated_db):
    user_id, job_id = _seed(
        title="Backend Engineer",
        location="Remote, United States",
        remote=True,
        fingerprint="rescore-us-remote",
    )

    assert _rescore(user_id) == 1

    score, state = _score_and_state(user_id, job_id)
    assert "limited to" in score.disqualifier
    assert state.status == JobStatus.REJECTED


def test_rescore_without_a_profile_is_a_noop(isolated_db):
    with Session(engine) as session:
        user = User(email="no-profile@applycanary.test", password_hash="dummy")
        session.add(user)
        session.commit()
        session.refresh(user)
        user_id = user.id

        assert asyncio_run(rescore_scored(session, user_id=user_id, limit=10)) == 0
