"""Tests for the profile API's partial-update semantics.

A PUT that sends a subset of fields used to reset every omitted field to its
model default — one client saving just the name silently wiped target titles,
locations and skills. These tests pin the fixed behaviour: omitted means
"leave unchanged".
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, select

from app.auth import SESSION_COOKIE_NAME, create_session_token, hash_password
from app.config import get_settings
from app.db import engine, init_db
from app.main import create_app
from app.models import InviteCode, Profile, User

PASSWORD = "correct-horse-battery"


@pytest.fixture
def isolated_db():
    """Clean the user/invite tables so each test starts empty.

    Mirrors the fixture in test_auth.py; tables are created explicitly because
    these tests build TestClient without a context manager (no lifespan)."""
    init_db()
    with Session(engine) as session:
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


@pytest.fixture
def client_with_profile(isolated_db):
    """A signed-in client whose user has a profile with pre-existing values."""
    init_db()
    with Session(engine) as session:
        user = User(email="me@example.com", password_hash=hash_password(PASSWORD))
        session.add(user)
        session.commit()
        session.refresh(user)
        session.add(
            Profile(
                user_id=user.id,
                email="me@example.com",
                full_name="Old Name",
                location="Old City",
                target_titles=["Old Title"],
                target_locations=["Old Location"],
                skills=["python"],
                base_resume_text="resume text that must survive",
            )
        )
        session.commit()
        token = create_session_token(user)

    client = TestClient(create_app(), follow_redirects=False)
    client.cookies.set(SESSION_COOKIE_NAME, token)
    return client


def test_partial_put_leaves_omitted_fields_untouched(client_with_profile):
    res = client_with_profile.put("/api/profile", json={"full_name": "New Name"})
    assert res.status_code == 200
    body = res.json()
    assert body["full_name"] == "New Name"
    # Everything not sent keeps its stored value.
    assert body["location"] == "Old City"
    assert body["target_titles"] == ["Old Title"]
    assert body["target_locations"] == ["Old Location"]
    assert body["skills"] == ["python"]


def test_partial_put_updates_only_the_sent_list_field(client_with_profile):
    res = client_with_profile.put(
        "/api/profile", json={"target_titles": ["Backend Engineer"]}
    )
    assert res.status_code == 200
    body = res.json()
    assert body["target_titles"] == ["Backend Engineer"]
    assert body["target_locations"] == ["Old Location"]
    assert body["full_name"] == "Old Name"


def test_partial_put_never_touches_the_resume(client_with_profile):
    res = client_with_profile.put(
        "/api/profile", json={"location": "Nairobi, Kenya"}
    )
    assert res.status_code == 200
    assert res.json()["location"] == "Nairobi, Kenya"
    # Resume text lives outside ProfileOut, so verify through a fresh session.
    with Session(engine) as session:
        from sqlmodel import select

        profile = session.exec(select(Profile)).first()
        assert profile.base_resume_text == "resume text that must survive"
