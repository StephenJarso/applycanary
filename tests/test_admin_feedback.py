from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, select

from app.db import engine
from app.main import create_app
from app.models import Feedback, User


@pytest.fixture
def client():
    app = create_app()
    with TestClient(app) as c:
        yield c


def _make_user(email: str, is_admin: bool = False) -> User:
    with Session(engine) as session:
        u = session.exec(select(User).where(User.email == email)).first()
        if not u:
            u = User(email=email, password_hash="dummy", is_admin=is_admin, is_active=True, email_verified=True)
            session.add(u)
            session.commit()
            session.refresh(u)
        return u


def test_public_feedback_submission(client):
    res = client.post("/api/public/feedback", json={
        "name": "Alice",
        "email": "alice@example.com",
        "category": "feature",
        "message": "Please add dark mode toggle!",
    })
    assert res.status_code == 200
    assert res.json()["ok"] is True

    with Session(engine) as session:
        fb = session.exec(select(Feedback).where(Feedback.email == "alice@example.com")).first()
        assert fb is not None
        assert fb.category == "feature"
        assert fb.message == "Please add dark mode toggle!"


def test_admin_feedback_and_status(client):
    admin = _make_user("admin@applycanary.test", is_admin=True)
    normal = _make_user("user@applycanary.test", is_admin=False)

    # Public feedback entry
    client.post("/api/public/feedback", json={
        "name": "Bob",
        "email": "bob@example.com",
        "category": "bug",
        "message": "Found a typo on the landing page",
    })

    # Unauthenticated admin access rejected
    res = client.get("/api/admin/feedback")
    assert res.status_code in (401, 303)

    # Login as regular user -> 403 Forbidden
    from app.auth import SESSION_COOKIE_NAME, create_session_token
    token_normal = create_session_token(normal)
    client.cookies.set(SESSION_COOKIE_NAME, token_normal)

    forbidden = client.get("/api/admin/feedback")
    assert forbidden.status_code == 403

    forbidden_status = client.get("/api/admin/system-status")
    assert forbidden_status.status_code == 403

    # Login as admin -> 200 OK
    token_admin = create_session_token(admin)
    client.cookies.set(SESSION_COOKIE_NAME, token_admin)

    res_fb = client.get("/api/admin/feedback")
    assert res_fb.status_code == 200
    data = res_fb.json()
    assert data["total"] >= 1
    item = next(it for it in data["items"] if it["email"] == "bob@example.com")
    assert item["status"] == "new"

    # Patch status
    res_patch = client.patch(f"/api/admin/feedback/{item['id']}", json={
        "status": "resolved",
        "admin_notes": "Fixed in latest commit",
    })
    assert res_patch.status_code == 200

    # System status
    res_sys = client.get("/api/admin/system-status")
    assert res_sys.status_code == 200
    sys_data = res_sys.json()
    assert sys_data["database"]["ok"] is True
    assert "users" in sys_data
    assert "llm" in sys_data
    assert "connectors" in sys_data
    assert sys_data["users"]["admin"] >= 1
