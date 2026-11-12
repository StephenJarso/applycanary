"""Curated programmes are database rows, not source code.

They used to be Python literals, so every new internship or volunteer call was
a deploy. The constants are now only a seed; the admin API owns the table.
"""

from __future__ import annotations

from datetime import date

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, select

import app.opportunities as opp
from app.auth import SESSION_COOKIE_NAME, create_session_token
from app.db import engine, init_db
from app.main import create_app
from app.models import ProgrammeEntry, User
from app.opportunities import (
    curated_cards,
    deadline_state,
    programme_item,
    seed_programmes,
)

TITLE = "Pytest Fellowship 2027"


@pytest.fixture
def db():
    init_db()
    yield
    with Session(engine) as session:
        for row in session.exec(
            select(ProgrammeEntry).where(
                ProgrammeEntry.title.in_([TITLE, f"{TITLE} edited"])
            )
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


def test_admin_can_edit_a_card_in_place(admin_client):
    created = admin_client.post(
        "/api/admin/programmes", json={"kind": "Internship", "title": TITLE}
    ).json()

    response = admin_client.patch(
        f"/api/admin/programmes/{created['id']}",
        json={"org": "Renamed Org", "closes": "2020-01-01"},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["org"] == "Renamed Org"
    assert body["expired"] is True


def test_editing_into_another_card_is_a_conflict(admin_client):
    first = admin_client.post(
        "/api/admin/programmes", json={"kind": "Internship", "title": TITLE}
    ).json()
    second = admin_client.post(
        "/api/admin/programmes",
        json={"kind": "Internship", "title": f"{TITLE} edited"},
    ).json()

    response = admin_client.patch(
        f"/api/admin/programmes/{second['id']}",
        json={"title": first["title"]},
    )
    assert response.status_code == 409


def test_editing_to_a_blank_title_is_rejected(admin_client):
    created = admin_client.post(
        "/api/admin/programmes", json={"kind": "Internship", "title": TITLE}
    ).json()
    response = admin_client.patch(
        f"/api/admin/programmes/{created['id']}", json={"title": "  "}
    )
    assert response.status_code == 422


class TestSeedMerge:
    """The seed runs on every boot, so new cards must land without clobbering."""

    def test_new_builtins_reach_an_already_seeded_database(self, db, monkeypatch):
        monkeypatch.setitem(
            opp._BUILTIN_CARDS,
            "Internship",
            [{"kind": "Internship", "title": TITLE, "org": "Pytest"}],
        )
        with Session(engine) as session:
            assert seed_programmes(session) >= 1
            assert seed_programmes(session) == 0

        assert TITLE in [card["title"] for card in curated_cards("Internship")]

    def test_an_edit_survives_the_seed(self, db, monkeypatch):
        monkeypatch.setitem(
            opp._BUILTIN_CARDS,
            "Internship",
            [{"kind": "Internship", "title": TITLE, "org": "Original"}],
        )
        with Session(engine) as session:
            seed_programmes(session)
            row = session.exec(
                select(ProgrammeEntry).where(ProgrammeEntry.title == TITLE)
            ).one()
            row.org = "Edited"
            session.add(row)
            session.commit()

        with Session(engine) as session:
            assert seed_programmes(session) == 0
            row = session.exec(
                select(ProgrammeEntry).where(ProgrammeEntry.title == TITLE)
            ).one()
            assert row.org == "Edited"

    def test_a_deleted_builtin_does_not_resurrect(self, db, monkeypatch, admin_client):
        monkeypatch.setitem(
            opp._BUILTIN_CARDS,
            "Internship",
            [{"kind": "Internship", "title": TITLE}],
        )
        with Session(engine) as session:
            seed_programmes(session)
            entry_id = session.exec(
                select(ProgrammeEntry).where(ProgrammeEntry.title == TITLE)
            ).one().id

        assert admin_client.delete(f"/api/admin/programmes/{entry_id}").status_code == 200
        with Session(engine) as session:
            assert seed_programmes(session) == 0

        assert TITLE not in [card["title"] for card in curated_cards("Internship")]

    def test_the_expanded_catalogue_is_seeded(self, db):
        with Session(engine) as session:
            seed_programmes(session)

        volunteers = {card["title"] for card in curated_cards("Volunteer")}
        internships = {card["title"] for card in curated_cards("Internship")}
        programmes = {card["title"] for card in curated_cards("Paid program")}
        apprenticeships = {
            card["title"]: card for card in curated_cards("Apprenticeship")
        }

        assert "UN Online Volunteering" in volunteers
        assert "Safaricom Graduate Management Trainee" in internships
        assert "CNCF Mentoring" in programmes
        assert "Zone01 Kisumu" in apprenticeships
        zone01 = apprenticeships["Zone01 Kisumu"]
        assert zone01["paid_to_learn"] is True
        assert zone01["accommodation"] == "not_stated"
        assert zone01["stipend_amount"] is None, "an unpublished stipend is not guessed"

    def test_structured_funding_reaches_the_public_shape(self, db):
        with Session(engine) as session:
            seed_programmes(session)

        igalia = next(
            card for card in curated_cards("Paid program")
            if card["title"] == "Igalia Coding Experience"
        )
        assert igalia["stipend_amount"] == 7000
        assert igalia["stipend_currency"] == "EUR"
        assert igalia["remote_friendly"] is True
        assert igalia["paid_to_learn"] is True


class TestDeadlineState:
    def test_a_past_deadline_is_expired(self):
        state = deadline_state(
            {"closes": "2026-01-01"}, today=date(2026, 2, 1)
        )
        assert state["expired"] is True
        assert state["days_left"] == -31
        assert state["status"] == "closed"

    def test_a_future_deadline_is_open(self):
        state = deadline_state(
            {"closes": "2026-12-24"}, today=date(2026, 12, 1)
        )
        assert state["expired"] is False
        assert state["days_left"] == 23
        assert state["status"] == "open"

    def test_rolling_windows_carry_no_deadline(self):
        state = deadline_state({"closes": "Rolling"})
        assert state["expired"] is False
        assert state["days_left"] is None
        assert state["status"] == "rolling"

    def test_a_future_open_date_is_opening_soon(self):
        state = deadline_state(
            {"opens": "2027-02-01", "closes": "2027-04-03"},
            today=date(2026, 12, 1),
        )
        assert state["status"] == "opening_soon"
        assert state["opens_in"] == 62

    def test_a_published_open_date_in_the_past_is_open(self):
        state = deadline_state(
            {"opens": "2026-11-01", "closes": "2026-12-21"},
            today=date(2026, 11, 12),
        )
        assert state["status"] == "open"

    def test_a_card_carries_its_own_state(self, db):
        with Session(engine) as session:
            session.add(_row(closes="2020-01-01"))
            session.commit()
            row = session.exec(
                select(ProgrammeEntry).where(ProgrammeEntry.title == TITLE)
            ).one()
            card = programme_item(row)

        assert card["expired"] is True
        assert card["days_left"] < 0
