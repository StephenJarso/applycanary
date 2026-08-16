"""Email alert + digest tests.

The scheduler previously called ``send_alert(job, score, profile=...)`` and
``send_digest(session, profile=...)`` against signatures that accepted neither
keyword — a TypeError swallowed by the scheduler guard, so high-score alerts
were *never* sent. These tests pin the fixed contract: per-user recipients
(profile email -> account email -> digest override -> operator DIGEST_TO) and a
digest scoped to the user's own activity.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from sqlmodel import Session, select

from app.auth import hash_password
from app.db import engine, init_db
from app.models import (
    Application,
    Job,
    JobScore,
    JobStatus,
    Profile,
    User,
    UserJob,
    utcnow,
)
from app.notify import email as notify


@pytest.fixture(autouse=True)
def db():
    init_db()
    with Session(engine) as session:
        for table in (Application, JobScore, UserJob, Profile, Job, User):
            for row in session.exec(select(table)).all():
                session.delete(row)
        session.commit()

        u1 = User(email="one@example.com", password_hash=hash_password("pw"))
        u2 = User(email="two@example.com", password_hash=hash_password("pw"))
        session.add_all([u1, u2])
        session.commit()
        session.refresh(u1)
        session.refresh(u2)
        # u1 leaves the profile email blank -> recipient falls back to account email.
        session.add_all([
            Profile(user_id=u1.id, email="", alert_min_score=85.0),
            Profile(user_id=u2.id, email="two@example.com"),
        ])

        j1 = Job(fingerprint="e1", source="test", company="One Co", title="Role A", status=JobStatus.NEW)
        j2 = Job(fingerprint="e2", source="test", company="Two Co", title="Role B", status=JobStatus.NEW)
        j3 = Job(fingerprint="e3", source="test", company="Three Co", title="Role C", status=JobStatus.NEW)
        session.add_all([j1, j2, j3])
        session.commit()
        session.refresh(j1)
        session.refresh(j2)
        session.refresh(j3)

        now = utcnow()
        session.add_all([
            JobScore(user_id=u1.id, job_id=j1.id, total=92.0),
            JobScore(user_id=u1.id, job_id=j2.id, total=55.0),
            # A strong match for u2 only: must never appear in u1's digest.
            JobScore(user_id=u2.id, job_id=j3.id, total=95.0),
            Application(user_id=u1.id, job_id=j1.id, submitted_at=now),
            UserJob(user_id=u1.id, job_id=j2.id, status=JobStatus.QUEUED),
        ])
        session.commit()
    yield


def asyncio_run(coro):
    import asyncio

    return asyncio.get_event_loop().run_until_complete(coro)


def _first_user() -> User:
    with Session(engine) as session:
        return session.exec(select(User).where(User.email == "one@example.com")).one()


def test_recipient_falls_back_to_account_email():
    """Empty profile email -> the user's account email is used."""
    with Session(engine) as session:
        user = _first_user()
        profile = session.exec(select(Profile).where(Profile.user_id == user.id)).one()
    assert notify._recipient(profile, user) == "one@example.com"


def test_recipient_prefers_profile_email():
    with Session(engine) as session:
        user = session.exec(select(User).where(User.email == "two@example.com")).one()
        profile = session.exec(select(Profile).where(Profile.user_id == user.id)).one()
    assert notify._recipient(profile, user) == "two@example.com"


def test_send_alert_signature_and_recipient(monkeypatch):
    """The scheduler's call shape (profile=) must not TypeError, and the mail
    goes to the user's own address."""
    captured: dict = {}

    async def fake_send(subject: str, html: str, text: str, *, to: str = "") -> bool:
        captured["to"] = to
        captured["subject"] = subject
        return True

    monkeypatch.setattr(notify, "send", fake_send)

    with Session(engine) as session:
        user = _first_user()
        profile = session.exec(select(Profile).where(Profile.user_id == user.id)).one()
        job = session.exec(select(Job).where(Job.company == "One Co")).one()
        score = session.exec(select(JobScore).where(JobScore.job_id == job.id)).one()

        ok = asyncio_run(notify.send_alert(job, score, profile=profile, user=user))

    assert ok
    assert captured["to"] == "one@example.com"
    assert "Role A" in captured["subject"]


def test_digest_is_scoped_to_the_user(monkeypatch):
    captured: dict = {}

    async def fake_send(subject: str, html: str, text: str, *, to: str = "") -> bool:
        captured["to"] = to
        captured["text"] = text
        return True

    monkeypatch.setattr(notify, "send", fake_send)

    with Session(engine) as session:
        user = _first_user()
        profile = session.exec(select(Profile).where(Profile.user_id == user.id)).one()
        ok = asyncio_run(notify.send_digest(session, profile=profile, user=user))

    assert ok
    assert captured["to"] == "one@example.com"
    # u1's applied + queued roles are reported...
    assert "Role A" in captured["text"]
    assert "Role B" in captured["text"]
    # ...but u2's strong match is not leaked into u1's digest.
    assert "Role C" not in captured["text"]


def test_digest_empty_profile_has_no_send(monkeypatch):
    calls: list[str] = []

    async def fake_send(subject: str, html: str, text: str, *, to: str = "") -> bool:
        calls.append(subject)
        return True

    monkeypatch.setattr(notify, "send", fake_send)

    with Session(engine) as session:
        user = _first_user()
        profile = session.exec(select(Profile).where(Profile.user_id == user.id)).one()
        # Move the user's activity outside the digest window.
        for row in session.exec(select(Application)).all():
            row.submitted_at = utcnow() - timedelta(days=3)
            session.add(row)
        for row in session.exec(select(UserJob)).all():
            row.status = JobStatus.NEW
            session.add(row)
        # The strong match (Role A, score 92) is new-match eligible via
        # first_seen_at; move the posting outside the window too.
        job = session.exec(select(Job).where(Job.company == "One Co")).one()
        job.first_seen_at = utcnow() - timedelta(days=3)
        session.add(job)
        session.commit()
        ok = asyncio_run(notify.send_digest(session, profile=profile, user=user))

    assert ok is False
    assert calls == []
