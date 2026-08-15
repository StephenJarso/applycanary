"""Tests for password hashing, session tokens, and the auth middleware."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, select

from app.auth import (
    SESSION_COOKIE_NAME,
    create_session_token,
    hash_password,
    parse_session_token,
    verify_password,
)
from app.config import get_settings
from app.db import engine, init_db
from app.main import create_app
from app.models import InviteCode, Profile, User, utcnow

PASSWORD = "correct-horse-battery"


@pytest.fixture
def isolated_db():
    """Create the schema and hand back a clean `user` table.

    The database is already redirected to a temp dir by conftest. Tables are
    created explicitly because these tests build `TestClient` without a context
    manager, so the startup lifespan that would call `init_db` never runs.
    """
    init_db()
    with Session(engine) as session:
        # Children before parents: SQLite enforces foreign keys here
        # (PRAGMA foreign_keys=ON in app/db.py), so deleting users while
        # invite_code.used_by_id still points at them raises IntegrityError.
        for row in session.exec(select(Profile)).all():
            session.delete(row)
        for row in session.exec(select(InviteCode)).all():
            session.delete(row)
        session.commit()
        for row in session.exec(select(User)).all():
            session.delete(row)
        session.commit()
    yield
    get_settings.cache_clear()


def _make_user(*, email: str = "user@example.com", admin: bool = False) -> User:
    with Session(engine) as session:
        user = User(
            email=email, password_hash=hash_password(PASSWORD), is_admin=admin
        )
        session.add(user)
        session.commit()
        session.refresh(user)
        return user


# ------------------------------------------------------------------ hashing


def test_password_round_trip():
    stored = hash_password(PASSWORD)
    assert stored != PASSWORD
    assert verify_password(PASSWORD, stored)
    assert not verify_password("wrong", stored)


def test_hash_is_salted():
    """Two hashes of one password must differ, or equal hashes leak equal
    passwords across accounts."""
    assert hash_password(PASSWORD) != hash_password(PASSWORD)


def test_verify_rejects_malformed_hash():
    for junk in ("", "not-a-hash", "scrypt$onlytwo"):
        assert not verify_password(PASSWORD, junk)


# ------------------------------------------------------------- session token


def test_session_token_round_trip(isolated_db):
    user = _make_user()
    parsed = parse_session_token(create_session_token(user))
    assert parsed == (user.id, user.token_version)


def test_session_token_rejects_tampering(isolated_db):
    user = _make_user()
    token = create_session_token(user)
    assert parse_session_token("invalid_token") is None
    assert parse_session_token(token[:-4] + "AAAA") is None


def test_token_version_bump_invalidates_cookie(isolated_db):
    """A password change must log out sessions issued before it."""
    user = _make_user()
    token = create_session_token(user)

    app = create_app()
    client = TestClient(app, follow_redirects=False)
    client.cookies.set(SESSION_COOKIE_NAME, token)
    assert client.get("/api/jobs").status_code == 200

    with Session(engine) as session:
        row = session.get(User, user.id)
        row.token_version += 1
        session.add(row)
        session.commit()

    client.cookies.set(SESSION_COOKIE_NAME, token)
    assert client.get("/api/jobs").status_code == 401


def test_inactive_user_is_rejected(isolated_db):
    user = _make_user()
    token = create_session_token(user)

    with Session(engine) as session:
        row = session.get(User, user.id)
        row.is_active = False
        session.add(row)
        session.commit()

    client = TestClient(create_app(), follow_redirects=False)
    client.cookies.set(SESSION_COOKIE_NAME, token)
    assert client.get("/api/jobs").status_code == 401


# ------------------------------------------------------------------ gateway


def test_unauthenticated_requests_are_blocked(isolated_db):
    client = TestClient(create_app(), follow_redirects=False)

    assert client.get("/health").status_code == 200

    res_ui = client.get("/")
    assert res_ui.status_code == 303
    assert res_ui.headers["location"] == "/login"

    res_api = client.get("/api/jobs")
    assert res_api.status_code == 401


def test_login_and_logout(isolated_db):
    _make_user(email="me@example.com")
    client = TestClient(create_app(), follow_redirects=False)

    res_bad = client.post(
        "/api/auth/login", json={"email": "me@example.com", "password": "wrong"}
    )
    assert res_bad.status_code == 401
    assert SESSION_COOKIE_NAME not in res_bad.cookies

    res_good = client.post(
        "/api/auth/login", json={"email": "me@example.com", "password": PASSWORD}
    )
    assert res_good.status_code == 200
    assert SESSION_COOKIE_NAME in res_good.cookies

    assert client.get("/api/jobs").status_code == 200

    client.post("/api/auth/logout")
    assert client.get("/api/jobs").status_code == 401


def test_login_is_case_insensitive_on_email(isolated_db):
    _make_user(email="me@example.com")
    client = TestClient(create_app(), follow_redirects=False)
    res = client.post(
        "/api/auth/login", json={"email": "ME@Example.COM ", "password": PASSWORD}
    )
    assert res.status_code == 200
    assert SESSION_COOKIE_NAME in res.cookies


# ----------------------------------------------------------------- register


def _make_invite(code: str = "GOODCODE") -> None:
    with Session(engine) as session:
        session.add(InviteCode(code=code))
        session.commit()


def test_register_requires_valid_invite(isolated_db):
    client = TestClient(create_app(), follow_redirects=False)
    res = client.post(
        "/api/auth/register",
        json={
            "email": "new@example.com",
            "password": "a-long-enough-pw",
            "invite_code": "NOPE",
        },
    )
    assert res.status_code == 400
    assert "invite" in res.json()["detail"].lower()

    with Session(engine) as session:
        assert session.exec(select(User)).first() is None


def test_register_consumes_invite_and_creates_profile(isolated_db):
    _make_invite()
    client = TestClient(create_app(), follow_redirects=False)

    res = client.post(
        "/api/auth/register",
        json={
            "email": "New@Example.com",
            "password": "a-long-enough-pw",
            "invite_code": "GOODCODE",
        },
    )
    assert res.status_code == 201
    assert SESSION_COOKIE_NAME in res.cookies

    with Session(engine) as session:
        user = session.exec(select(User)).one()
        assert user.email == "new@example.com"
        assert verify_password("a-long-enough-pw", user.password_hash)

        invite = session.exec(select(InviteCode)).one()
        assert invite.used_by_id == user.id
        assert not invite.is_redeemable()

        profile = session.exec(select(Profile)).one()
        assert profile.user_id == user.id


def test_invite_cannot_be_reused(isolated_db):
    _make_invite()
    client = TestClient(create_app(), follow_redirects=False)
    payload = {"password": "a-long-enough-pw", "invite_code": "GOODCODE"}

    first = client.post("/api/auth/register", json={"email": "one@example.com", **payload})
    assert first.status_code == 201

    second = client.post("/api/auth/register", json={"email": "two@example.com", **payload})
    assert second.status_code == 400
    assert "invite" in second.json()["detail"].lower()

    with Session(engine) as session:
        assert len(session.exec(select(User)).all()) == 1


def test_expired_invite_is_refused(isolated_db):
    with Session(engine) as session:
        session.add(
            InviteCode(
                code="STALE",
                expires_at=utcnow().replace(year=utcnow().year - 1),
            )
        )
        session.commit()

    client = TestClient(create_app(), follow_redirects=False)
    res = client.post(
        "/api/auth/register",
        json={
            "email": "late@example.com",
            "password": "a-long-enough-pw",
            "invite_code": "STALE",
        },
    )
    assert res.status_code == 400
    assert "invite" in res.json()["detail"].lower()


def test_register_rejects_duplicate_email_and_short_password(isolated_db):
    _make_user(email="taken@example.com")
    _make_invite()
    client = TestClient(create_app(), follow_redirects=False)

    dup = client.post(
        "/api/auth/register",
        json={
            "email": "taken@example.com",
            "password": "a-long-enough-pw",
            "invite_code": "GOODCODE",
        },
    )
    assert dup.status_code == 409
    assert "already" in dup.json()["detail"].lower()

    short = client.post(
        "/api/auth/register",
        json={"email": "new@example.com", "password": "short", "invite_code": "GOODCODE"},
    )
    assert short.status_code == 422  # pydantic min_length=10 on password

    # Neither failure may burn the invite.
    with Session(engine) as session:
        assert session.exec(select(InviteCode)).one().is_redeemable()
