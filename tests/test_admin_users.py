"""Admin user-management guards.

The users table gives every administrator a Demote / Deactivate / Delete button,
including their own row. Acting on yourself is a one-click lockout, so the API
refuses it regardless of how many other admins exist, and the UI hides those
buttons on the caller's own row.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, select

from app.auth import SESSION_COOKIE_NAME, create_session_token
from app.db import engine
from app.main import create_app
from app.models import User


@pytest.fixture
def client():
    app = create_app()
    with TestClient(app) as c:
        yield c


@pytest.fixture(autouse=True)
def isolated_admins(client):
    """Start each test with no admins, so the first one created is the owner.

    Depends on ``client`` so the app has run ``sync_schema()`` and the tables
    exist before we touch them.
    """
    with Session(engine) as session:
        for u in session.exec(select(User)).all():
            u.is_admin = False
            u.is_active = True
            session.add(u)
        session.commit()
    yield
    with Session(engine) as session:
        for u in session.exec(select(User)).all():
            u.is_admin = False
            session.add(u)
        session.commit()


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


def test_admin_cannot_demote_themselves(client):
    admin = _make_user("self-demote@applycanary.test", is_admin=True)
    other = _make_user("other-admin@applycanary.test", is_admin=True)
    assert other.is_admin

    _login(client, admin)
    res = client.patch(
        f"/api/admin/users/{admin.id}/role", json={"is_admin": False}
    )

    assert res.status_code == 400
    assert "own admin role" in res.json()["detail"]


def test_admin_cannot_deactivate_themselves(client):
    admin = _make_user("self-deactivate@applycanary.test", is_admin=True)

    _login(client, admin)
    res = client.patch(
        f"/api/admin/users/{admin.id}/role", json={"is_active": False}
    )

    assert res.status_code == 400
    assert "deactivate your own account" in res.json()["detail"]


def test_admin_cannot_delete_themselves(client):
    admin = _make_user("self-delete@applycanary.test", is_admin=True)

    _login(client, admin)
    res = client.delete(f"/api/admin/users/{admin.id}")

    assert res.status_code == 400
    assert "own account" in res.json()["detail"]


def test_admin_can_still_manage_another_user(client):
    admin = _make_user("manager@applycanary.test", is_admin=True)
    member = _make_user("member@applycanary.test")

    _login(client, admin)
    promote = client.patch(
        f"/api/admin/users/{member.id}/role", json={"is_admin": True}
    )
    assert promote.status_code == 200

    demote = client.patch(
        f"/api/admin/users/{member.id}/role", json={"is_admin": False}
    )
    assert demote.status_code == 200


def test_superuser_is_protected_from_other_admins(client):
    owner = _make_user("owner@applycanary.test", is_admin=True)
    other = _make_user("second-admin@applycanary.test", is_admin=True)

    _login(client, other)
    demote = client.patch(
        f"/api/admin/users/{owner.id}/role", json={"is_admin": False}
    )
    deactivate = client.patch(
        f"/api/admin/users/{owner.id}/role", json={"is_active": False}
    )
    delete = client.delete(f"/api/admin/users/{owner.id}")

    assert demote.status_code == 400
    assert "superuser" in demote.json()["detail"]
    assert deactivate.status_code == 400
    assert "superuser" in deactivate.json()["detail"]
    assert delete.status_code == 400
    assert "superuser" in delete.json()["detail"]


def test_system_status_marks_the_superuser(client):
    owner = _make_user("owner-status@applycanary.test", is_admin=True)
    _make_user("second-status@applycanary.test", is_admin=True)

    _login(client, owner)
    res = client.get("/api/admin/system-status")

    assert res.status_code == 200
    rows = {row["id"]: row for row in res.json()["users"]["user_list"]}
    assert rows[owner.id]["is_superuser"] is True
    assert sum(1 for row in rows.values() if row["is_superuser"]) == 1
