"""Tests for password hashing, session tokens, and the auth middleware."""

from __future__ import annotations

from datetime import timedelta

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


def _make_user(*, email: str = "user@example.com", admin: bool = False, verified: bool = True) -> User:
    with Session(engine) as session:
        user = User(
            email=email, password_hash=hash_password(PASSWORD), is_admin=admin, email_verified=verified
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


def test_register_open_signup(isolated_db):
    """No invite code at all: signup is open, and a Profile is still created."""
    client = TestClient(create_app(), follow_redirects=False)
    res = client.post(
        "/api/auth/register",
        json={"email": "new@example.com", "password": "a-long-enough-pw"},
    )
    assert res.status_code == 201
    assert SESSION_COOKIE_NAME in res.cookies

    with Session(engine) as session:
        user = session.exec(select(User)).one()
        assert user.email == "new@example.com"
        assert session.exec(select(Profile)).one().user_id == user.id
        assert session.exec(select(InviteCode)).all() == []


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


def test_register_with_default_invite_code_does_not_consume_a_row(isolated_db, monkeypatch):
    """A shared DEFAULT_INVITE_CODE is accepted for every registrant without
    touching the invite_code table, so it never runs out. A supplied code that
    matches nothing is still rejected."""
    monkeypatch.setenv("DEFAULT_INVITE_CODE", "HACKATHON2026")
    get_settings.cache_clear()
    client = TestClient(create_app(), follow_redirects=False)
    payload = {"password": "a-long-enough-pw", "invite_code": "HACKATHON2026"}

    for email in ["one@example.com", "two@example.com"]:
        res = client.post("/api/auth/register", json={"email": email, **payload})
        assert res.status_code == 201, res.text

    # No invite rows were created or consumed for either registration.
    with Session(engine) as session:
        assert session.exec(select(InviteCode)).all() == []
        assert len(session.exec(select(User)).all()) == 2

    # A wrong code is still rejected even with the default configured.
    bad = client.post(
        "/api/auth/register",
        json={"email": "three@example.com", "password": "a-long-enough-pw", "invite_code": "NOPE"},
    )
    assert bad.status_code == 400


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


# --------------------------------------------------- emailed-token flows


@pytest.fixture
def outbox(monkeypatch):
    """Capture auth emails instead of sending them.

    The raw token only ever exists in the email — the database stores an HMAC of
    it — so intercepting the send is the only way a test can follow a link the
    way a user would.
    """
    sent: list[tuple[str, tuple]] = []

    def make(kind: str):
        async def fake(*args):
            sent.append((kind, args))
            return True
        return fake

    for name in (
        "send_verification_email", "send_password_reset_email",
        "send_email_change_email", "send_password_changed_email",
        "send_email_changed_email",
    ):
        monkeypatch.setattr(f"app.api.auth.{name}", make(name))
    return sent


def _token(outbox: list[tuple[str, tuple]], kind: str) -> str:
    """The token argument of the last email of `kind`."""
    matches = [args for sent_kind, args in outbox if sent_kind == kind]
    assert matches, f"no {kind} was sent; outbox held {[k for k, _ in outbox]}"
    return matches[-1][1]


def _register(client: TestClient, email: str = "new@example.com") -> object:
    _make_invite()
    return client.post(
        "/api/auth/register",
        json={"email": email, "password": "a-long-enough-pw", "invite_code": "GOODCODE"},
    )


def test_registration_sends_a_confirmation_link_that_verifies_once(isolated_db, outbox):
    client = TestClient(create_app(), follow_redirects=False)
    assert _register(client).status_code == 201

    with Session(engine) as session:
        assert session.exec(select(User)).one().email_verified is False

    token = _token(outbox, "send_verification_email")
    assert client.post("/api/auth/verify-email", json={"token": token}).status_code == 204

    with Session(engine) as session:
        assert session.exec(select(User)).one().email_verified is True

    # Single use: the token is cleared, so replaying the link cannot re-verify.
    replay = client.post("/api/auth/verify-email", json={"token": token})
    assert replay.status_code == 400


def test_verify_email_rejects_a_password_reset_token(isolated_db, outbox):
    """The two token kinds must not be interchangeable.

    They shared one column at first, which let a reset token — obtainable by
    anyone who knows an address — satisfy the verification endpoint.
    """
    user = _make_user(email="me@example.com", verified=False)
    client = TestClient(create_app(), follow_redirects=False)

    assert client.post("/api/auth/forgot-password", json={"email": "me@example.com"}).status_code == 204
    reset_token = _token(outbox, "send_password_reset_email")

    assert client.post("/api/auth/verify-email", json={"token": reset_token}).status_code == 400
    with Session(engine) as session:
        assert session.get(User, user.id).email_verified is False


def test_password_reset_sets_the_new_password_and_logs_sessions_out(isolated_db, outbox):
    user = _make_user(email="me@example.com")
    stale_cookie = create_session_token(user)

    client = TestClient(create_app(), follow_redirects=False)
    assert client.post("/api/auth/forgot-password", json={"email": "me@example.com"}).status_code == 204
    token = _token(outbox, "send_password_reset_email")

    new_password = "a-brand-new-password"
    assert client.post(
        "/api/auth/reset-password", json={"token": token, "password": new_password}
    ).status_code == 204

    # A cookie minted before the reset must stop working.
    client.cookies.set(SESSION_COOKIE_NAME, stale_cookie)
    assert client.get("/api/jobs").status_code == 401
    client.cookies.clear()

    assert client.post(
        "/api/auth/login", json={"email": "me@example.com", "password": PASSWORD}
    ).status_code == 401
    assert client.post(
        "/api/auth/login", json={"email": "me@example.com", "password": new_password}
    ).status_code == 200

    # The owner is told, so an unwanted reset is noticed.
    assert any(kind == "send_password_changed_email" for kind, _ in outbox)


def test_reset_token_is_single_use(isolated_db, outbox):
    _make_user(email="me@example.com")
    client = TestClient(create_app(), follow_redirects=False)
    client.post("/api/auth/forgot-password", json={"email": "me@example.com"})
    token = _token(outbox, "send_password_reset_email")

    first = client.post("/api/auth/reset-password", json={"token": token, "password": "first-new-password"})
    assert first.status_code == 204
    second = client.post("/api/auth/reset-password", json={"token": token, "password": "second-new-password"})
    assert second.status_code == 400


def test_expired_reset_token_is_rejected(isolated_db, outbox):
    user = _make_user(email="me@example.com")
    client = TestClient(create_app(), follow_redirects=False)
    client.post("/api/auth/forgot-password", json={"email": "me@example.com"})
    token = _token(outbox, "send_password_reset_email")

    with Session(engine) as session:
        row = session.get(User, user.id)
        row.password_reset_expires_at = utcnow() - timedelta(minutes=1)
        session.add(row)
        session.commit()

    res = client.post("/api/auth/reset-password", json={"token": token, "password": "a-long-enough-pw"})
    assert res.status_code == 400
    with Session(engine) as session:
        assert verify_password(PASSWORD, session.get(User, user.id).password_hash)


def test_forgot_password_does_not_disclose_whether_an_email_exists(isolated_db, outbox):
    """Same 204 either way — a 404 here would be an account-enumeration oracle."""
    _make_user(email="real@example.com")
    client = TestClient(create_app(), follow_redirects=False)

    known = client.post("/api/auth/forgot-password", json={"email": "real@example.com"})
    unknown = client.post("/api/auth/forgot-password", json={"email": "ghost@example.com"})
    assert known.status_code == unknown.status_code == 204

    # Only the real address is actually mailed.
    recipients = [args[0] for kind, args in outbox if kind == "send_password_reset_email"]
    assert recipients == ["real@example.com"]


def test_unverified_login_is_allowed_by_default(isolated_db):
    """Out of the box an unconfirmed account may still sign in.

    The frontend nudges instead, so switching the feature on cannot lock out
    accounts that were created before it existed.
    """
    _make_user(email="pending@example.com", verified=False)
    client = TestClient(create_app(), follow_redirects=False)
    res = client.post("/api/auth/login", json={"email": "pending@example.com", "password": PASSWORD})
    assert res.status_code == 200
    assert res.json()["email_verified"] is False


def test_enforced_verification_blocks_login_with_403(isolated_db, monkeypatch):
    monkeypatch.setenv("REQUIRE_EMAIL_VERIFICATION", "true")
    get_settings.cache_clear()
    _make_user(email="pending@example.com", verified=False)
    _make_user(email="ok@example.com", verified=True)
    client = TestClient(create_app(), follow_redirects=False)

    blocked = client.post("/api/auth/login", json={"email": "pending@example.com", "password": PASSWORD})
    assert blocked.status_code == 403
    assert SESSION_COOKIE_NAME not in blocked.cookies

    allowed = client.post("/api/auth/login", json={"email": "ok@example.com", "password": PASSWORD})
    assert allowed.status_code == 200


def test_enforced_verification_withholds_the_session_at_registration(isolated_db, outbox, monkeypatch):
    monkeypatch.setenv("REQUIRE_EMAIL_VERIFICATION", "true")
    get_settings.cache_clear()
    client = TestClient(create_app(), follow_redirects=False)

    res = _register(client)
    assert res.status_code == 201
    # Auto-login here would make the gate meaningless.
    assert res.json()["session_started"] is False
    assert SESSION_COOKIE_NAME not in res.cookies

    # Confirming the address then lets the account in.
    token = _token(outbox, "send_verification_email")
    assert client.post("/api/auth/verify-email", json={"token": token}).status_code == 204
    assert client.post(
        "/api/auth/login", json={"email": "new@example.com", "password": "a-long-enough-pw"}
    ).status_code == 200


def test_email_change_moves_the_address_only_after_the_new_one_confirms(isolated_db, outbox):
    _make_user(email="old@example.com")
    client = TestClient(create_app(), follow_redirects=False)
    assert client.post(
        "/api/auth/login", json={"email": "old@example.com", "password": PASSWORD}
    ).status_code == 200

    assert client.post(
        "/api/auth/change-email", json={"new_email": "new@example.com", "password": PASSWORD}
    ).status_code == 204

    # Staged, not applied.
    with Session(engine) as session:
        row = session.exec(select(User)).one()
        assert row.email == "old@example.com"
        assert row.pending_email == "new@example.com"

    # The link goes to the address being claimed, not the current one.
    change_recipients = [args[0] for kind, args in outbox if kind == "send_email_change_email"]
    assert change_recipients == ["new@example.com"]

    token = _token(outbox, "send_email_change_email")
    assert client.post("/api/auth/verify-email-change", json={"token": token}).status_code == 204

    with Session(engine) as session:
        row = session.exec(select(User)).one()
        assert row.email == "new@example.com"
        assert row.pending_email == ""
        assert row.email_verified is True

    # Both the old and the new address are notified.
    notified = {args[0] for kind, args in outbox if kind == "send_email_changed_email"}
    assert notified == {"old@example.com", "new@example.com"}


def test_change_email_requires_the_current_password(isolated_db, outbox):
    _make_user(email="me@example.com")
    client = TestClient(create_app(), follow_redirects=False)
    client.post("/api/auth/login", json={"email": "me@example.com", "password": PASSWORD})

    res = client.post(
        "/api/auth/change-email", json={"new_email": "new@example.com", "password": "wrong"}
    )
    assert res.status_code == 401
    with Session(engine) as session:
        assert session.exec(select(User)).one().pending_email == ""


def test_change_email_refuses_an_address_already_registered(isolated_db, outbox):
    _make_user(email="me@example.com")
    _make_user(email="taken@example.com")
    client = TestClient(create_app(), follow_redirects=False)
    client.post("/api/auth/login", json={"email": "me@example.com", "password": PASSWORD})

    res = client.post(
        "/api/auth/change-email", json={"new_email": "taken@example.com", "password": PASSWORD}
    )
    assert res.status_code == 409


def test_emailed_token_endpoints_need_no_session(isolated_db, outbox):
    """These links are opened from a mail client, which carries no cookie."""
    client = TestClient(create_app(), follow_redirects=False)
    for path in ("/api/auth/verify-email", "/api/auth/verify-email-change", "/api/auth/reset-password"):
        body = {"token": "not-a-real-token"}
        if path.endswith("reset-password"):
            body["password"] = "a-long-enough-pw"
        res = client.post(path, json=body)
        # 400 (bad token) rather than 401 (no session) is the point here.
        assert res.status_code == 400, f"{path} returned {res.status_code}"


# ------------------------------------------------ auth disabled (local mode)


@pytest.fixture
def auth_off(isolated_db, monkeypatch):
    """Serve the app with authentication disabled (the local default)."""
    monkeypatch.setenv("AUTH_ENABLED", "false")
    monkeypatch.delenv("AUTH_PASSWORD", raising=False)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def test_auth_disabled_serves_app_without_login(auth_off):
    """With AUTH_ENABLED=false the dashboard and API answer without a session.

    A shared local account is attached on demand, so per-user queries still have
    an owner instead of silently seeing another user's data.
    """
    client = TestClient(create_app(), follow_redirects=False)

    assert client.get("/health").status_code == 200
    assert client.get("/api/jobs").status_code == 200
    # Served, not bounced to /login with a 303.
    assert client.get("/").status_code == 200

    with Session(engine) as session:
        local_users = session.exec(select(User).where(User.is_admin.is_(True))).all()
        assert len(local_users) == 1
        assert local_users[0].email == "local@applycanary.local"


def test_auth_disabled_reuses_the_same_local_account(auth_off):
    """Repeated unauthenticated requests must not mint a new user each time."""
    client = TestClient(create_app(), follow_redirects=False)
    for _ in range(3):
        assert client.get("/api/jobs").status_code == 200

    with Session(engine) as session:
        assert len(session.exec(select(User)).all()) == 1


def test_auth_disabled_registers_without_a_code(auth_off, outbox):
    """Open signup with auth off: no invite code, straight into the app."""
    client = TestClient(create_app(), follow_redirects=False)
    # Hitting a guarded route first attaches the shared local account.
    assert client.get("/api/jobs").status_code == 200

    res = client.post(
        "/api/auth/register",
        json={"email": "new@example.com", "password": "a-long-enough-pw"},
    )
    assert res.status_code == 201
    with Session(engine) as session:
        assert len(session.exec(select(User)).all()) == 2  # local + registered


def test_auth_reenabled_rejects_anonymous_requests(isolated_db):
    """Guard against an AUTH_ENABLED flip silently exposing the data."""
    client = TestClient(create_app(), follow_redirects=False)
    assert client.get("/api/jobs").status_code == 401
    res_ui = client.get("/")
    assert res_ui.status_code == 303
    assert res_ui.headers["location"] == "/login"
