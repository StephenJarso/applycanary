"""Tests for the follow-up reminder job (``_send_follow_up_reminders``)."""

from __future__ import annotations

from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session

from app.auth import SESSION_COOKIE_NAME, create_session_token, hash_password
from app.db import engine, init_db
from app.main import create_app
from app.models import (
    Application,
    Job,
    Profile,
    PushSubscription,
    User,
    utcnow,
)
from app.scheduler import _send_follow_up_reminders

# Rows this module creates, removed afterwards: the shared test database holds
# other modules' jobs with dependent rows and is not ours to empty.
_CREATED: list[tuple[type, int]] = []


@pytest.fixture
def clean_db():
    init_db()
    _CREATED.clear()
    try:
        yield
    finally:
        with Session(engine) as session:
            for model, pk in reversed(_CREATED):
                row = session.get(model, pk)
                if row is not None:
                    session.delete(row)
            session.commit()


def _user(email: str = "followups@example.com") -> User:
    with Session(engine) as session:
        user = User(
            email=email,
            password_hash=hash_password("secret123"),
            email_verified=True,
            is_active=True,
        )
        session.add(user)
        session.commit()
        session.refresh(user)
        _CREATED.append((User, user.id or 0))
        return user


def _client(user: User) -> TestClient:
    client = TestClient(create_app(), follow_redirects=False)
    client.cookies.set(SESSION_COOKIE_NAME, create_session_token(user))
    return client


def _subscribe(user: User, endpoint: str = "https://push.example/one") -> PushSubscription:
    with Session(engine) as session:
        sub = PushSubscription(
            user_id=user.id or 0, endpoint=endpoint, p256dh="key", auth="auth"
        )
        session.add(sub)
        session.commit()
        session.refresh(sub)
        _CREATED.append((PushSubscription, sub.id or 0))
        return sub


def _application(
    user: User,
    *,
    title: str = "Go Engineer",
    due_in_days: int | None = -1,
    submitted_days_ago: int = 9,
    response_received: bool = False,
    outcome: str = "",
) -> Application:
    with Session(engine) as session:
        job = Job(
            fingerprint=f"fp-{title}-{user.email}",
            source="test",
            company="Acme",
            title=title,
            apply_url="https://acme.example/apply",
        )
        session.add(job)
        session.commit()
        session.refresh(job)
        app_row = Application(
            user_id=user.id,
            job_id=job.id or 0,
            submitted_at=utcnow() - timedelta(days=submitted_days_ago),
            follow_up_due=(
                None if due_in_days is None else utcnow() + timedelta(days=due_in_days)
            ),
            response_received=response_received,
            outcome=outcome,
        )
        session.add(app_row)
        session.commit()
        session.refresh(app_row)
        _CREATED.append((Job, job.id or 0))
        _CREATED.append((Application, app_row.id or 0))
        return app_row


def _notified(app_row: Application) -> bool:
    with Session(engine) as session:
        row = session.get(Application, app_row.id)
        assert row is not None
        return row.follow_up_notified_at is not None


@pytest.mark.asyncio
async def test_a_due_follow_up_pushes_once(clean_db, monkeypatch):
    user = _user()
    _subscribe(user)
    _application(user)

    calls: list[dict] = []

    def fake_push(**kwargs):  # noqa: ANN003
        calls.append(kwargs)
        return "ok"

    monkeypatch.setattr("app.scheduler.send_push", fake_push)

    await _send_follow_up_reminders()

    assert len(calls) == 1
    assert calls[0]["url"] == "/pipeline"
    assert "Go Engineer at Acme" in calls[0]["body"]
    assert "9 days ago" in calls[0]["body"]

    # A second run the same day must not repeat itself.
    await _send_follow_up_reminders()
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_a_future_follow_up_is_left_alone(clean_db, monkeypatch):
    user = _user()
    _subscribe(user)
    _application(user, due_in_days=3)

    monkeypatch.setattr(
        "app.scheduler.send_push", lambda **kw: pytest.fail("should not push")
    )

    await _send_follow_up_reminders()


@pytest.mark.asyncio
async def test_answered_and_closed_applications_are_not_chased(clean_db, monkeypatch):
    user = _user()
    _subscribe(user)
    _application(user, title="Replied Role", response_received=True)
    _application(user, title="Rejected Role", outcome="rejected")
    plain = _application(user, title="Still Waiting")

    pushed: list[str] = []

    def fake_push(**kwargs):  # noqa: ANN003
        pushed.append(kwargs["body"])
        return "ok"

    monkeypatch.setattr("app.scheduler.send_push", fake_push)

    await _send_follow_up_reminders()

    assert len(pushed) == 1
    assert "Still Waiting" in pushed[0]
    assert _notified(plain) is True


@pytest.mark.asyncio
async def test_a_failed_push_leaves_the_reminder_armed(clean_db, monkeypatch):
    user = _user()
    _subscribe(user)
    app_row = _application(user)

    monkeypatch.setattr("app.scheduler.send_push", lambda **kw: "error")

    await _send_follow_up_reminders()

    # Nothing was delivered, so the next run should try again rather than
    # silently marking the application as reminded.
    assert _notified(app_row) is False


@pytest.mark.asyncio
async def test_a_stale_subscription_is_removed(clean_db, monkeypatch):
    user = _user()
    _subscribe(user, endpoint="https://push.example/stale")
    _application(user)

    monkeypatch.setattr("app.scheduler.send_push", lambda **kw: "stale")

    await _send_follow_up_reminders()

    with Session(engine) as session:
        subs = session.query(PushSubscription).filter_by(user_id=user.id).all()
        assert subs == []


@pytest.mark.asyncio
async def test_email_is_sent_when_the_user_has_their_own_address(clean_db, monkeypatch):
    user = _user()
    with Session(engine) as session:
        profile = Profile(user_id=user.id or 0, email="me@example.com")
        session.add(profile)
        session.commit()
        session.refresh(profile)
        _CREATED.append((Profile, profile.id or 0))
    app_row = _application(user)

    emailed: list[int | None] = []

    async def fake_email(job, *, profile=None, user=None, days_waiting=None):  # noqa: ANN001, ANN202
        emailed.append(days_waiting)
        return True

    monkeypatch.setattr("app.notify.email.send_follow_up_reminder", fake_email)
    monkeypatch.setattr("app.scheduler.send_push", lambda **kw: "error")

    await _send_follow_up_reminders()

    assert emailed == [9]
    assert _notified(app_row) is True


@pytest.mark.asyncio
async def test_rescheduling_rearms_the_reminder(clean_db, monkeypatch):
    user = _user()
    _subscribe(user)
    app_row = _application(user)
    monkeypatch.setattr("app.scheduler.send_push", lambda **kw: "ok")
    await _send_follow_up_reminders()
    assert _notified(app_row) is True

    res = _client(user).patch(
        f"/api/pipeline/{app_row.job_id}", json={"follow_up_in_days": 5}
    )

    assert res.status_code == 200
    assert res.json()["follow_up_due"] is not None
    assert _notified(app_row) is False
