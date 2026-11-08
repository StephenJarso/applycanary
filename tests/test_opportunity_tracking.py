"""Tests for tracking applications to internships, programs and volunteer calls."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session

from app.api.router import _opportunity_stage
from app.auth import SESSION_COOKIE_NAME, create_session_token, hash_password
from app.db import engine, init_db
from app.main import create_app
from app.models import User, WatchedOpportunity, utcnow

_CREATED: list[tuple[type, int]] = []

OPP = {
    "url": "https://example.com/hennge-gip",
    "title": "HENNGE Global Internship",
    "org": "HENNGE",
    "kind": "Internship",
    "deadline": "2027-01-31",
}


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


def _user(email: str = "track@example.com") -> User:
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


def _row(user: User) -> WatchedOpportunity | None:
    with Session(engine) as session:
        row = (
            session.query(WatchedOpportunity)
            .filter_by(user_id=user.id, opp_url=OPP["url"])
            .first()
        )
        if row is not None:
            session.expunge(row)
        return row


# ---------------------------------------------------------- stage derivation


def test_stage_derivation():
    watched = WatchedOpportunity(
        user_id=1, opp_url="https://example.com/x", watched=True
    )
    assert _opportunity_stage(watched) == "watching"

    applied = WatchedOpportunity(
        user_id=1, opp_url="https://example.com/x", applied_at=utcnow()
    )
    assert _opportunity_stage(applied) == "applied"

    replied = WatchedOpportunity(
        user_id=1,
        opp_url="https://example.com/x",
        applied_at=utcnow(),
        response_received=True,
    )
    assert _opportunity_stage(replied) == "responded"

    closed = WatchedOpportunity(
        user_id=1,
        opp_url="https://example.com/x",
        applied_at=utcnow(),
        response_received=True,
        outcome="rejected",
    )
    assert _opportunity_stage(closed) == "rejected"


# ------------------------------------------------------------- the tracker


def test_marking_applied_tracks_without_turning_on_the_alert(clean_db):
    user = _user()
    client = _client(user)

    res = client.patch(
        "/api/opportunities/tracked", json={**OPP, "status": "applied"}
    )

    assert res.status_code == 200
    assert res.json()["stage"] == "applied"
    assert res.json()["watched"] is False
    # Marking applied is not a request for deadline pushes.
    assert client.get("/api/opportunities/watched").json() == []
    assert [i["opp_url"] for i in client.get("/api/opportunities/tracked").json()] == [
        OPP["url"]
    ]


def test_watching_an_already_tracked_opportunity_keeps_the_tracking(clean_db):
    user = _user()
    client = _client(user)
    client.patch("/api/opportunities/tracked", json={**OPP, "status": "applied"})

    client.post("/api/opportunities/watch", json=OPP)

    row = _row(user)
    assert row is not None
    assert row.watched is True
    assert row.applied_at is not None
    # The alert list and the tracker both see it now.
    assert len(client.get("/api/opportunities/watched").json()) == 1
    assert client.get("/api/opportunities/tracked").json()[0]["stage"] == "applied"


def test_turning_off_the_alert_keeps_the_application_history(clean_db):
    user = _user()
    client = _client(user)
    client.post("/api/opportunities/watch", json=OPP)
    client.patch("/api/opportunities/tracked", json={**OPP, "status": "applied"})

    res = client.request("DELETE", "/api/opportunities/watch", json={"url": OPP["url"]})

    assert res.json() == {"ok": True, "watched": False}
    assert client.get("/api/opportunities/watched").json() == []
    tracked = client.get("/api/opportunities/tracked").json()
    assert [i["stage"] for i in tracked] == ["applied"]
    row = _row(user)
    assert row is not None and row.watched is False


def test_an_alert_only_row_is_not_an_application(clean_db):
    user = _user()
    client = _client(user)

    client.post("/api/opportunities/watch", json=OPP)

    assert client.get("/api/opportunities/tracked").json() == []


def test_heard_back_and_verdicts_follow_the_job_tracker_rules(clean_db):
    user = _user()
    client = _client(user)

    replied = client.patch(
        "/api/opportunities/tracked", json={**OPP, "status": "heard_back"}
    ).json()
    assert replied["stage"] == "responded"
    assert replied["response_received"] is True
    # Hearing back implies an application, even if none was marked first.
    assert replied["applied_at"] is not None

    interview = client.patch(
        "/api/opportunities/tracked", json={**OPP, "status": "interview"}
    ).json()
    assert interview["stage"] == "interview"
    assert interview["response_received"] is True

    ghosted = client.patch(
        "/api/opportunities/tracked", json={**OPP, "status": "ghosted"}
    ).json()
    assert ghosted["stage"] == "ghosted"
    assert ghosted["response_received"] is False


def test_reopening_withdraws_the_verdict_but_keeps_the_application(clean_db):
    user = _user()
    client = _client(user)
    client.patch("/api/opportunities/tracked", json={**OPP, "status": "rejected"})

    item = client.patch(
        "/api/opportunities/tracked", json={**OPP, "status": ""}
    ).json()

    assert item["stage"] == "responded"  # they did reply; only the verdict went
    assert item["outcome"] == ""
    assert item["applied_at"] is not None


def test_another_users_tracking_is_invisible(clean_db):
    owner = _user("owner-opp@example.com")
    stranger = _user("stranger-opp@example.com")
    _client(owner).patch(
        "/api/opportunities/tracked", json={**OPP, "status": "applied"}
    )

    stranger_client = _client(stranger)
    assert stranger_client.get("/api/opportunities/tracked").json() == []
    assert stranger_client.get("/api/opportunities/watched").json() == []

    # The same posting tracked by the stranger is their own row.
    stranger_client.patch(
        "/api/opportunities/tracked", json={**OPP, "status": "interview"}
    )
    assert _client(owner).get("/api/opportunities/tracked").json()[0]["stage"] == "applied"


def test_a_too_short_url_is_rejected(clean_db):
    user = _user()

    res = _client(user).patch(
        "/api/opportunities/tracked", json={"url": "x", "status": "applied"}
    )

    assert res.status_code == 422
