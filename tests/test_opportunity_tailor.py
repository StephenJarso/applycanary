from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, select

from app.auth import SESSION_COOKIE_NAME, create_session_token
from app.db import engine, init_db
from app.main import create_app
from app.models import Profile, User


@pytest.fixture(autouse=True)
def clean_db():
    init_db()
    with Session(engine) as session:
        for p in session.exec(select(Profile)).all():
            session.delete(p)
        for u in session.exec(select(User)).all():
            session.delete(u)
        session.commit()


def test_tailor_opportunity_unauthenticated():
    client = TestClient(create_app())
    res = client.post(
        "/api/actions/tailor-opportunity",
        json={"title": "HENNGE Global Internship Program (GIP)", "org": "HENNGE K.K."},
    )
    assert res.status_code == 401


def test_tailor_opportunity_authenticated_hennge():
    with Session(engine) as session:
        user = User(
            email="candidate@example.com",
            password_hash="pw",
            email_verified=True,
            is_active=True,
        )
        session.add(user)
        session.commit()
        session.refresh(user)

        profile = Profile(
            user_id=user.id,
            full_name="Stephen Jarso",
            location="Nairobi, Kenya",
            skills=["Python", "Go", "Docker", "REST APIs"],
            base_resume_text="Software engineer with backend experience in Python and Go microservices.",
            motivation_statement="Dedicated to building scalable cloud systems and experiencing engineering culture in Tokyo.",
        )
        session.add(profile)
        session.commit()
        token = create_session_token(user)

    client = TestClient(create_app())
    client.cookies.set(SESSION_COOKIE_NAME, token)

    res = client.post(
        "/api/actions/tailor-opportunity",
        json={
            "title": "HENNGE Global Internship Program (GIP)",
            "org": "HENNGE K.K.",
            "kind": "Internship",
            "description": "Full-Stack and backend engineering in Go and Python in Shibuya, Tokyo.",
            "tags": ["Go", "Python", "Tokyo", "Japan"],
        },
    )
    assert res.status_code == 200
    data = res.json()
    assert data["ok"] is True
    assert "Stephen Jarso" in data["tailored_resume"]
    assert "HENNGE K.K." in data["tailored_resume"]
    assert "Go" in data["matched_skills"] or "Python" in data["matched_skills"]
    # Check that HENNGE strategy is included
    assert "Coding Challenge" in data["tailored_resume"] or "100%" in data["tailored_resume"]


def test_tailor_opportunity_outreachy():
    with Session(engine) as session:
        user = User(
            email="outreachy@example.com",
            password_hash="pw",
            email_verified=True,
            is_active=True,
        )
        session.add(user)
        session.commit()
        session.refresh(user)

        profile = Profile(
            user_id=user.id,
            full_name="Jane Doe",
            skills=["Python", "Git", "Open Source"],
            base_resume_text="Junior developer contributing to open-source Python projects.",
        )
        session.add(profile)
        session.commit()
        token = create_session_token(user)

    client = TestClient(create_app())
    client.cookies.set(SESSION_COOKIE_NAME, token)

    res = client.post(
        "/api/actions/tailor-opportunity",
        json={
            "title": "Outreachy (May 2027 cohort)",
            "org": "Software Freedom Conservancy",
            "kind": "Internship",
            "description": "Paid remote internship in open source software.",
            "tags": ["Open Source", "Remote", "Python"],
        },
    )
    assert res.status_code == 200
    data = res.json()
    assert data["ok"] is True
    assert "Outreachy" in data["tailored_resume"]
    assert "Jane Doe" in data["tailored_resume"]
    # Outreachy portal reminder
    assert "Portal" in data["tailored_resume"] or "portal" in data["tailored_resume"]
