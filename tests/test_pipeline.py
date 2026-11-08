"""Tests for the application pipeline tracker (``/api/pipeline``)."""

from __future__ import annotations

from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session

from app.api.router import _pipeline_stage
from app.auth import SESSION_COOKIE_NAME, create_session_token, hash_password
from app.db import engine, init_db
from app.main import create_app
from app.models import Application, Job, User, utcnow

# (model, primary key) for every row this module created, removed afterwards.
# The shared test database is not ours to empty: earlier suites leave resume
# versions, interview preps and job aliases pointing at their own jobs, so a
# blanket DELETE FROM job fails the foreign-key check.
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


def _user(email: str = "pipeline@example.com") -> User:
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


def _job(user: User, *, submitted: bool = True, title: str = "Go Engineer") -> Application:
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
            submitted_at=utcnow() if submitted else None,
        )
        session.add(app_row)
        session.commit()
        session.refresh(app_row)
        _CREATED.append((Job, job.id or 0))
        _CREATED.append((Application, app_row.id or 0))
        return app_row


# ---------------------------------------------------------- stage derivation


def test_an_explicit_outcome_outranks_a_stale_follow_up():
    assert (
        _pipeline_stage("offer", True, utcnow() - timedelta(days=9), utcnow())
        == "offer"
    )


def test_a_reply_outranks_an_overdue_follow_up():
    assert _pipeline_stage("", True, utcnow() - timedelta(days=2), utcnow()) == "responded"


def test_a_passed_follow_up_date_is_the_follow_up_stage():
    assert (
        _pipeline_stage("", False, utcnow() - timedelta(hours=1), utcnow())
        == "follow-up due"
    )


def test_a_future_follow_up_leaves_the_application_in_applied():
    assert (
        _pipeline_stage("", False, utcnow() + timedelta(days=3), utcnow()) == "applied"
    )


def test_an_unsubmitted_application_is_prepared():
    assert _pipeline_stage("", False, None, None) == "prepared"


# ---------------------------------------------------------------- the list


def test_pipeline_lists_only_sent_applications(clean_db):
    user = _user()
    _job(user, submitted=True, title="Sent Role")
    _job(user, submitted=False, title="Prepared Role")

    body = _client(user).get("/api/pipeline").json()

    assert [item["title"] for item in body["items"]] == ["Sent Role"]
    assert body["items"][0]["stage"] == "applied"
    assert body["counts"]["applied"] == 1
    # Every stage key is present, so the header chips cannot vanish.
    assert set(body["counts"]) == {
        "follow-up due", "applied", "responded", "interview", "offer",
        "rejected", "ghosted",
    }


def test_an_overdue_follow_up_sorts_to_the_top(clean_db):
    user = _user()
    older = _job(user, title="Older Role")
    _job(user, title="Newer Role")
    with Session(engine) as session:
        row = session.get(Application, older.id)
        assert row is not None
        row.follow_up_due = utcnow() - timedelta(days=1)
        session.add(row)
        session.commit()

    items = _client(user).get("/api/pipeline").json()["items"]

    assert items[0]["title"] == "Older Role"
    assert items[0]["stage"] == "follow-up due"
    assert items[0]["follow_up_overdue"] is True


# ------------------------------------------------------------ one click


def test_recording_an_interview_ends_the_chase(clean_db):
    user = _user()
    app_row = _job(user)
    client = _client(user)

    res = client.patch(f"/api/pipeline/{app_row.job_id}", json={"outcome": "interview"})

    assert res.status_code == 200
    item = res.json()
    assert item["stage"] == "interview"
    assert item["response_received"] is True
    assert item["follow_up_due"] is None
    # And it sticks in the list, not just in the response.
    assert client.get("/api/pipeline").json()["items"][0]["stage"] == "interview"


def test_ghosted_is_not_a_reply(clean_db):
    user = _user()
    app_row = _job(user)

    item = _client(user).patch(
        f"/api/pipeline/{app_row.job_id}", json={"outcome": "ghosted"}
    ).json()

    assert item["stage"] == "ghosted"
    assert item["response_received"] is False


def test_follow_up_is_scheduled_then_cleared(clean_db):
    user = _user()
    app_row = _job(user)
    client = _client(user)

    scheduled = client.patch(
        f"/api/pipeline/{app_row.job_id}", json={"follow_up_in_days": 5}
    ).json()
    due = scheduled["follow_up_due"]
    assert due is not None
    assert scheduled["stage"] == "applied"  # five days out is not overdue yet

    cleared = client.patch(
        f"/api/pipeline/{app_row.job_id}", json={"follow_up_in_days": 0}
    ).json()
    assert cleared["follow_up_due"] is None


def test_a_reply_clears_a_pending_follow_up(clean_db):
    user = _user()
    app_row = _job(user)
    client = _client(user)
    client.patch(f"/api/pipeline/{app_row.job_id}", json={"follow_up_in_days": 5})

    item = client.patch(
        f"/api/pipeline/{app_row.job_id}", json={"response_received": True}
    ).json()

    assert item["stage"] == "responded"
    assert item["follow_up_due"] is None


def test_reopening_a_closed_application_returns_it_to_waiting(clean_db):
    user = _user()
    app_row = _job(user)
    client = _client(user)
    client.patch(f"/api/pipeline/{app_row.job_id}", json={"outcome": "rejected"})

    item = client.patch(f"/api/pipeline/{app_row.job_id}", json={"outcome": ""}).json()

    # Only the verdict is withdrawn. The employer did reply, so the row lands
    # in "responded" rather than pretending nothing ever came back.
    assert item["stage"] == "responded"
    assert item["outcome"] == ""
    assert item["response_received"] is True


# ------------------------------------------------------- per-user scoping


def test_another_users_application_is_invisible_and_unwritable(clean_db):
    owner = _user("owner@example.com")
    stranger = _user("stranger@example.com")
    app_row = _job(owner)

    stranger_client = _client(stranger)
    assert stranger_client.get("/api/pipeline").json()["items"] == []
    blocked = stranger_client.patch(
        f"/api/pipeline/{app_row.job_id}", json={"outcome": "offer"}
    )
    assert blocked.status_code == 404

    # The owner's row is untouched by the failed attempt.
    owner_item = _client(owner).get("/api/pipeline").json()["items"][0]
    assert owner_item["stage"] == "applied"
    assert owner_item["outcome"] == ""


def test_a_prepared_application_cannot_be_staged(clean_db):
    user = _user()
    app_row = _job(user, submitted=False)

    res = _client(user).patch(
        f"/api/pipeline/{app_row.job_id}", json={"outcome": "interview"}
    )

    assert res.status_code == 404
