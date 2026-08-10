"""Tests for authentication and session token verification."""

from __future__ import annotations

import base64

import pytest
from fastapi.testclient import TestClient

from app.auth import create_session_token, validate_session_token
from app.config import get_settings
from app.db import init_db
from app.main import create_app


@pytest.fixture
def isolated_db():
    """Ensure the schema exists before a test boots the app.

    The database itself is already redirected to a temp dir by conftest. Tables
    are created explicitly because these tests build `TestClient` without a
    context manager, so the startup lifespan that would normally call `init_db`
    never runs, and the endpoints below query tables like `job`.
    """
    init_db()
    yield
    get_settings.cache_clear()


def test_session_token_lifecycle():
    token = create_session_token("admin")
    assert token is not None
    user = validate_session_token(token)
    assert user == "admin"

    assert validate_session_token("invalid_token") is None


def test_auth_disabled_by_default(isolated_db, monkeypatch):
    monkeypatch.setenv("AUTH_ENABLED", "false")
    monkeypatch.setenv("AUTH_PASSWORD", "")
    get_settings.cache_clear()

    app = create_app()
    client = TestClient(app)

    # All endpoints should be accessible without credentials
    res = client.get("/")
    assert res.status_code == 200

    res_api = client.get("/api/jobs")
    assert res_api.status_code == 200

    res_health = client.get("/health")
    assert res_health.status_code == 200
    get_settings.cache_clear()


def test_auth_required_blocks_unauthenticated(isolated_db, monkeypatch):
    monkeypatch.setenv("AUTH_ENABLED", "true")
    monkeypatch.setenv("AUTH_USERNAME", "admin")
    monkeypatch.setenv("AUTH_PASSWORD", "secret123")
    get_settings.cache_clear()

    app = create_app()
    client = TestClient(app, follow_redirects=False)

    # Health check must remain unauthenticated
    res_health = client.get("/health")
    assert res_health.status_code == 200

    # UI redirects to login
    res_ui = client.get("/")
    assert res_ui.status_code == 303
    assert res_ui.headers["location"] == "/login"

    # API returns 401
    res_api = client.get("/api/jobs")
    assert res_api.status_code == 401

    # Basic Auth succeeds
    valid_auth = base64.b64encode(b"admin:secret123").decode()
    res_basic = client.get("/api/jobs", headers={"Authorization": f"Basic {valid_auth}"})
    assert res_basic.status_code == 200

    # Cookie auth succeeds
    token = create_session_token("admin")
    client.cookies.set("applycanary_session", token)
    res_cookie = client.get("/")
    assert res_cookie.status_code == 200

    get_settings.cache_clear()


def test_login_route(monkeypatch):
    monkeypatch.setenv("AUTH_ENABLED", "true")
    monkeypatch.setenv("AUTH_USERNAME", "admin")
    monkeypatch.setenv("AUTH_PASSWORD", "secret123")
    get_settings.cache_clear()

    app = create_app()
    client = TestClient(app, follow_redirects=False)

    # Invalid login
    res_bad = client.post("/login", data={"username": "admin", "password": "wrongpassword"})
    assert res_bad.status_code == 303
    assert "error=" in res_bad.headers["location"]

    # Valid login
    res_good = client.post("/login", data={"username": "admin", "password": "secret123"})
    assert res_good.status_code == 303
    assert res_good.headers["location"] == "/"
    assert "applycanary_session" in res_good.cookies

    get_settings.cache_clear()
