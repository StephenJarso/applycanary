"""Admin email-delivery diagnostics.

Brevo accepting a message ("email sent via Brevo") is not proof of delivery —
the receiving server decides that afterwards. These tests cover the admin
surface that reports Brevo's verdict for one recipient, so a "user never got
the verification email" report can be answered with evidence.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session

from app.auth import SESSION_COOKIE_NAME, create_session_token
from app.db import engine
from app.main import create_app
from app.models import User
from app.notify import email as mail


@pytest.fixture
def client():
    app = create_app()
    with TestClient(app) as c:
        yield c


def _make_user(email: str, *, is_admin: bool = False) -> User:
    with Session(engine) as session:
        u = User(
            email=email,
            password_hash="dummy",
            is_admin=is_admin,
            is_active=True,
            email_verified=True,
        )
        session.add(u)
        session.commit()
        session.refresh(u)
        return u


def _login(client: TestClient, user: User) -> None:
    client.cookies.set(SESSION_COOKIE_NAME, create_session_token(user))


def test_admin_sees_brevo_events_for_a_recipient(client, monkeypatch):
    admin = _make_user("email-admin@applycanary.test", is_admin=True)
    asked: list[str] = []

    async def fake_events(address: str, **_kwargs) -> list[dict[str, str]]:
        asked.append(address)
        return [
            {
                "date": "2026-10-21T15:36:42Z",
                "event": "delivered",
                "reason": "",
                "subject": "Verify your email address — ApplyCanary",
                "message_id": "<1@smtp-relay.mailin.fr>",
            },
            {
                "date": "2026-10-21T15:36:41Z",
                "event": "requests",
                "reason": "",
                "subject": "Verify your email address — ApplyCanary",
                "message_id": "<1@smtp-relay.mailin.fr>",
            },
        ]

    monkeypatch.setattr(mail, "brevo_delivery_events", fake_events)

    _login(client, admin)
    res = client.get("/api/admin/email-events?email=New.User@Example.com")

    assert res.status_code == 200
    body = res.json()
    assert body["ok"] is True
    assert body["email"] == "new.user@example.com"
    assert [e["event"] for e in body["events"]] == ["delivered", "requests"]
    assert asked == ["new.user@example.com"]
    # The operator needs the sender and link origin to judge deliverability;
    # both come back with the events so one request answers the whole question.
    assert "from" in body["config"]
    assert "link_base" in body["config"]


def test_events_lookup_reports_provider_errors_instead_of_hiding_them(client, monkeypatch):
    admin = _make_user("email-admin-err@applycanary.test", is_admin=True)

    async def broken(_address: str, **_kwargs) -> list[dict[str, str]]:
        raise RuntimeError("BREVO_API_KEY is not configured; delivery events need Brevo")

    monkeypatch.setattr(mail, "brevo_delivery_events", broken)

    _login(client, admin)
    res = client.get("/api/admin/email-events?email=someone@example.com")

    assert res.status_code == 200
    body = res.json()
    assert body["ok"] is False
    assert "BREVO_API_KEY" in body["error"]
    assert body["events"] == []


def test_non_admin_cannot_read_delivery_events(client, monkeypatch):
    member = _make_user("email-member@applycanary.test")

    async def fake_events(_address: str, **_kwargs) -> list[dict[str, str]]:
        return []

    monkeypatch.setattr(mail, "brevo_delivery_events", fake_events)

    _login(client, member)
    res = client.get("/api/admin/email-events?email=someone@example.com")

    assert res.status_code == 403


def test_system_status_exposes_sender_and_link_origin(client, monkeypatch):
    admin = _make_user("email-admin-status@applycanary.test", is_admin=True)

    _login(client, admin)
    res = client.get("/api/admin/system-status")

    assert res.status_code == 200
    email_block = res.json()["email"]
    assert set(email_block) >= {
        "configured", "backend", "from", "link_base", "verification_required",
    }
