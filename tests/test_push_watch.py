"""Tests for Web Push (VAPID) and opportunity watch functionality."""

from __future__ import annotations

import json
from datetime import date, timedelta
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, select

from app.auth import SESSION_COOKIE_NAME, create_session_token, hash_password
from app.config import get_settings
from app.db import engine, init_db
from app.main import create_app
from app.models import InviteCode, Profile, PushSubscription, User, UserJob, WatchedOpportunity
from app.notify.push import push_enabled, send_push
from app.scheduler import _send_deadline_pushes
from scripts.generate_vapid_keys import generate_vapid_keys


@pytest.fixture
def clean_db():
    init_db()
    with Session(engine) as session:
        for model in (WatchedOpportunity, PushSubscription, Profile, InviteCode, UserJob, User):
            for row in session.exec(select(model)).all():
                session.delete(row)
            session.commit()
    yield
    get_settings.cache_clear()


def _create_user(email: str = "test@example.com") -> User:
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
        return user


def _auth_client(user: User) -> TestClient:
    app = create_app()
    client = TestClient(app, follow_redirects=False)
    client.cookies.set(SESSION_COOKIE_NAME, create_session_token(user))
    return client


def test_generate_vapid_keys():
    priv, pub = generate_vapid_keys()
    assert isinstance(priv, str) and len(priv) > 20
    assert isinstance(pub, str) and len(pub) > 20


def test_push_enabled_toggle(monkeypatch):
    monkeypatch.setenv("VAPID_PRIVATE_KEY", "")
    monkeypatch.setenv("VAPID_PUBLIC_KEY", "")
    monkeypatch.setenv("VAPID_EMAIL", "")
    get_settings.cache_clear()
    assert not push_enabled()

    monkeypatch.setenv("VAPID_PRIVATE_KEY", "privkey123")
    monkeypatch.setenv("VAPID_PUBLIC_KEY", "pubkey123")
    monkeypatch.setenv("VAPID_EMAIL", "mailto:admin@example.com")
    get_settings.cache_clear()
    assert push_enabled()


def test_vapid_public_key_endpoint(clean_db, monkeypatch):
    monkeypatch.setenv("VAPID_PUBLIC_KEY", "test-public-key-xyz")
    get_settings.cache_clear()
    user = _create_user("vapid@example.com")
    client = _auth_client(user)
    res = client.get("/api/push/vapid-public-key")
    assert res.status_code == 200
    assert res.json() == {"publicKey": "test-public-key-xyz"}

    # Also works without auth when AUTH_ENABLED is false
    monkeypatch.setenv("AUTH_ENABLED", "false")
    get_settings.cache_clear()
    unauth_client = TestClient(create_app())
    res_unauth = unauth_client.get("/api/push/vapid-public-key")
    assert res_unauth.status_code == 200
    assert res_unauth.json() == {"publicKey": "test-public-key-xyz"}


def test_push_subscribe_and_unsubscribe(clean_db):
    user = _create_user("pusher@example.com")
    client = _auth_client(user)

    # Subscribe device
    res = client.post(
        "/api/push/subscribe",
        json={"endpoint": "https://push.example.com/ep1", "p256dh": "key1", "auth": "auth1"},
    )
    assert res.status_code == 200
    assert res.json() == {"ok": True}

    with Session(engine) as session:
        subs = session.exec(select(PushSubscription).where(PushSubscription.user_id == user.id)).all()
        assert len(subs) == 1
        assert subs[0].endpoint == "https://push.example.com/ep1"
        assert subs[0].p256dh == "key1"

    # Refresh subscription with updated keys
    res = client.post(
        "/api/push/subscribe",
        json={"endpoint": "https://push.example.com/ep1", "p256dh": "key2", "auth": "auth2"},
    )
    assert res.status_code == 200
    with Session(engine) as session:
        subs = session.exec(select(PushSubscription).where(PushSubscription.user_id == user.id)).all()
        assert len(subs) == 1
        assert subs[0].p256dh == "key2"

    # Unsubscribe
    res = client.delete("/api/push/unsubscribe")
    assert res.status_code == 200
    assert res.json() == {"ok": True}

    with Session(engine) as session:
        subs = session.exec(select(PushSubscription).where(PushSubscription.user_id == user.id)).all()
        assert len(subs) == 0


def test_watch_and_unwatch_opportunity(clean_db):
    user1 = _create_user("u1@example.com")
    user2 = _create_user("u2@example.com")

    client1 = _auth_client(user1)
    client2 = _auth_client(user2)

    # User 1 watches opp
    opp_data = {
        "url": "https://example.com/opp1",
        "title": "SWE Internship",
        "org": "TechCorp",
        "kind": "Internship",
        "deadline": "2026-12-01",
    }
    res = client1.post("/api/opportunities/watch", json=opp_data)
    assert res.status_code == 200
    assert res.json() == {"ok": True, "watched": True}

    # Calling watch again is idempotent
    res = client1.post("/api/opportunities/watch", json=opp_data)
    assert res.status_code == 200
    assert res.json() == {"ok": True, "watched": True}

    # Verify per-user scoping in get_watched
    res1 = client1.get("/api/opportunities/watched")
    assert res1.status_code == 200
    items1 = res1.json()
    assert len(items1) == 1
    assert items1[0]["opp_url"] == "https://example.com/opp1"
    assert items1[0]["opp_title"] == "SWE Internship"

    # User 2 sees nothing
    res2 = client2.get("/api/opportunities/watched")
    assert res2.status_code == 200
    assert res2.json() == []

    # User 1 unwatches
    res = client1.request("DELETE", "/api/opportunities/watch", json={"url": "https://example.com/opp1"})
    assert res.status_code == 200
    assert res.json() == {"ok": True, "watched": False}

    res1_after = client1.get("/api/opportunities/watched")
    assert res1_after.json() == []


def test_send_push_outcomes(monkeypatch):
    # When disabled
    monkeypatch.setenv("VAPID_PRIVATE_KEY", "")
    get_settings.cache_clear()
    assert send_push("https://ep", "k", "a", "t", "b") == "error"

    # When enabled and webpush succeeds
    monkeypatch.setenv("VAPID_PRIVATE_KEY", "priv")
    monkeypatch.setenv("VAPID_PUBLIC_KEY", "pub")
    monkeypatch.setenv("VAPID_EMAIL", "mailto:admin@example.com")
    get_settings.cache_clear()

    mock_webpush = MagicMock()
    with patch("pywebpush.webpush", mock_webpush):
        status = send_push("https://ep", "k", "a", "My Title", "My Body", "/test")
        assert status == "ok"
        mock_webpush.assert_called_once()
        args = mock_webpush.call_args[1]
        assert args["subscription_info"]["endpoint"] == "https://ep"
        payload = json.loads(args["data"])
        assert payload["title"] == "My Title"
        assert payload["body"] == "My Body"
        assert payload["url"] == "/test"

    # When 410 Gone is returned
    with patch("pywebpush.webpush", side_effect=Exception("Push failed: 410 Gone")):
        assert send_push("https://ep", "k", "a", "t", "b") == "stale"

    # When general error occurs
    with patch("pywebpush.webpush", side_effect=Exception("Connection reset")):
        assert send_push("https://ep", "k", "a", "t", "b") == "error"


@pytest.mark.asyncio
async def test_scheduler_send_deadline_pushes(clean_db, monkeypatch):
    monkeypatch.setenv("VAPID_PRIVATE_KEY", "priv")
    monkeypatch.setenv("VAPID_PUBLIC_KEY", "pub")
    monkeypatch.setenv("VAPID_EMAIL", "mailto:admin@example.com")
    get_settings.cache_clear()

    user = _create_user("alice@example.com")
    target_deadline = (date.today() + timedelta(days=5)).isoformat()
    other_deadline = (date.today() + timedelta(days=10)).isoformat()

    with Session(engine) as session:
        # User subscription
        session.add(
            PushSubscription(
                user_id=user.id,
                endpoint="https://push.example.com/alice",
                p256dh="key",
                auth="auth",
            )
        )
        # Opportunity closing in 5 days
        session.add(
            WatchedOpportunity(
                user_id=user.id,
                opp_url="https://example.com/target-opp",
                opp_title="Target Internship",
                opp_org="Acme",
                opp_kind="Internship",
                deadline=target_deadline,
            )
        )
        # Opportunity closing in 10 days (should NOT be notified)
        session.add(
            WatchedOpportunity(
                user_id=user.id,
                opp_url="https://example.com/future-opp",
                opp_title="Future Internship",
                opp_org="Beta",
                opp_kind="Internship",
                deadline=other_deadline,
            )
        )
        session.commit()

    with patch("app.scheduler.send_push", return_value="ok") as mock_push:
        await _send_deadline_pushes()
        assert mock_push.call_count == 1
        args = mock_push.call_args[1]
        assert args["endpoint"] == "https://push.example.com/alice"
        assert "Target Internship" in args["body"]

    # Verify notified_at is now set
    with Session(engine) as session:
        opp = session.exec(
            select(WatchedOpportunity).where(WatchedOpportunity.opp_url == "https://example.com/target-opp")
        ).first()
        assert opp.notified_at is not None

    # Running it again skips because already notified today
    with patch("app.scheduler.send_push", return_value="ok") as mock_push:
        await _send_deadline_pushes()
        assert mock_push.call_count == 0


@pytest.mark.asyncio
async def test_scheduler_cleans_stale_subscriptions(clean_db, monkeypatch):
    monkeypatch.setenv("VAPID_PRIVATE_KEY", "priv")
    monkeypatch.setenv("VAPID_PUBLIC_KEY", "pub")
    monkeypatch.setenv("VAPID_EMAIL", "mailto:admin@example.com")
    get_settings.cache_clear()

    user = _create_user("bob@example.com")
    target_deadline = (date.today() + timedelta(days=5)).isoformat()

    with Session(engine) as session:
        session.add(
            PushSubscription(
                user_id=user.id,
                endpoint="https://push.example.com/stale",
                p256dh="key",
                auth="auth",
            )
        )
        session.add(
            WatchedOpportunity(
                user_id=user.id,
                opp_url="https://example.com/opp",
                opp_title="Opp",
                opp_org="Corp",
                opp_kind="Internship",
                deadline=target_deadline,
            )
        )
        session.commit()

    with patch("app.scheduler.send_push", return_value="stale"):
        await _send_deadline_pushes()

    with Session(engine) as session:
        subs = session.exec(select(PushSubscription).where(PushSubscription.user_id == user.id)).all()
        assert len(subs) == 0
