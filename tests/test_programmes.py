"""Curated programmes are database rows, not source code.

They used to be Python literals, so every new internship or volunteer call was
a deploy. The constants are now only a seed; the admin API owns the table.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, select

from app.auth import SESSION_COOKIE_NAME, create_session_token
from app.db import engine, init_db
from app.main import create_app
from app.models import ProgrammeEntry, User
from app.opportunities import curated_cards, seed_programmes

TITLE = "Pytest Fellowship 2027"


@pytest.fixture
def db():
    init_db()
    yield
    with Session(engine) as session:
        for row in session.exec(
            select(ProgrammeEntry).where(ProgrammeEntry.title == TITLE)
        ).all():
            session.delete(row)
        for user in session.exec(
            select(User).where(User.email == "programmes-admin@example.test")
        ).all():
            session.delete(user)
        session.commit()


@pytest.fixture
def admin_client(db) -> TestClient:
    with Session(engine) as session:
        user = User(
            email="programmes-admin@example.test",
            password_hash="x",
            is_admin=True,
            email_verified=True,
            is_active=True,
        )
        session.add(user)
        session.commit()
        session.refresh(user)
    client = TestClient(create_app(), follow_redirects=False)
    client.cookies.set(SESSION_COOKIE_NAME, create_session_token(user))
    return client


def _row(**kwargs) -> ProgrammeEntry:
    fields = {
        "kind": "Paid program",
        "title": TITLE,
        "org": "Pytest",
        "when": "Coding June-Aug",
        "city": "Nairobi",
        "tags": ["Paid"],
        "source": "Official",
        "url": "https://example.test/fellowship",
        "desc": "A fellowship that only exists in the test database.",
    }
    fields.update(kwargs)
    return ProgrammeEntry(**fields)


def test_the_seed_is_idempotent(db):
    with Session(engine) as session:
        seed_programmes(session)
        second = seed_programmes(session)
    assert second == 0


def test_curated_cards_read_the_table(db):
    with Session(engine) as session:
        session.add(_row())
        session.commit()

    titles = [card["title"] for card in curated_cards("Paid program")]
    assert TITLE in titles


def test_curated_cards_keep_kinds_apart(db):
    with Session(engine) as session:
        session.add(_row(kind="Volunteer"))
        session.commit()

    assert TITLE not in [c["title"] for c in curated_cards("Internship")]
    assert TITLE in [c["title"] for c in curated_cards("Volunteer")]


def test_admin_can_create_and_delete_a_card(admin_client):
    response = admin_client.post(
        "/api/admin/programmes",
        json={
            "kind": "Internship",
            "title": TITLE,
            "org": "Pytest",
            "url": "https://example.test/internship",
        },
    )
    assert response.status_code == 200, response.text
    entry_id = response.json()["id"]

    listed = admin_client.get("/api/admin/programmes")
    assert listed.status_code == 200
    assert TITLE in [row["title"] for row in listed.json()]

    assert TITLE in [c["title"] for c in curated_cards("Internship")]

    deleted = admin_client.delete(f"/api/admin/programmes/{entry_id}")
    assert deleted.status_code == 200
    assert TITLE not in [c["title"] for c in curated_cards("Internship")]


def test_import_updates_an_existing_card(admin_client):
    payload = {"kind": "Paid program", "title": TITLE, "org": "Pytest"}
    first = admin_client.post("/api/admin/programmes/import", json={"items": [payload]})
    assert first.status_code == 200
    assert first.json()["detail"]["created"] == 1

    payload["org"] = "Pytest Renamed"
    second = admin_client.post("/api/admin/programmes/import", json={"items": [payload]})
    assert second.json()["detail"]["updated"] == 1

    with Session(engine) as session:
        row = session.exec(
            select(ProgrammeEntry).where(ProgrammeEntry.title == TITLE)
        ).one()
        assert row.org == "Pytest Renamed"


def test_a_non_http_url_is_rejected(admin_client):
    response = admin_client.post(
        "/api/admin/programmes",
        json={"kind": "Internship", "title": TITLE, "url": "javascript:alert(1)"},
    )
    assert response.status_code == 422


def test_deleting_a_missing_card_is_a_404(admin_client):
    assert admin_client.delete("/api/admin/programmes/999999").status_code == 404
