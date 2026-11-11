"""Boards added by URL: recognition, preview and the admin API.

The point of the feature is that a new board is a row, not a deploy, so the
tests cover the two things that can silently go wrong: a URL recognised as the
wrong platform (which 404s forever) and an unvalidated write.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, select

from app.auth import SESSION_COOKIE_NAME, create_session_token
from app.db import engine, init_db
from app.main import create_app
from app.models import CustomBoard, User
from app.sources import boards
from app.sources.base import BaseSource, RawJob

URL = "https://boards.greenhouse.io/pytestco"


@pytest.fixture
def db():
    init_db()
    yield
    with Session(engine) as session:
        for row in session.exec(
            select(CustomBoard).where(CustomBoard.url.in_([URL, "https://example.test/careers"]))
        ).all():
            session.delete(row)
        for user in session.exec(
            select(User).where(User.email == "boards-admin@example.test")
        ).all():
            session.delete(user)
        session.commit()


@pytest.fixture
def admin_client(db) -> TestClient:
    with Session(engine) as session:
        user = User(
            email="boards-admin@example.test",
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


class _FakeSource(BaseSource):
    name = "fake-board"

    def __init__(self, jobs: list[RawJob], **kwargs: object) -> None:
        super().__init__(**kwargs)  # type: ignore[arg-type]
        self._jobs = jobs

    async def fetch(self) -> list[RawJob]:
        return self._jobs


class TestRecognise:
    @pytest.mark.parametrize(
        ("url", "kind", "token"),
        [
            ("https://boards.greenhouse.io/acme", "greenhouse", "acme"),
            ("https://job-boards.greenhouse.io/acme/jobs/1", "greenhouse", "acme"),
            ("https://jobs.lever.co/acme", "lever", "acme"),
            ("https://jobs.ashbyhq.com/acme", "ashby", "acme"),
            ("https://apply.workable.com/acme/j/ABC/", "workable", "acme"),
            ("https://acme.workable.com/jobs/1", "workable", "acme"),
            ("https://careers.smartrecruiters.com/Acme", "smartrecruiters", "Acme"),
        ],
    )
    def test_known_platforms(self, url, kind, token):
        assert boards.recognise(url) == (kind, token)

    def test_unknown_page_becomes_a_careers_source(self):
        assert boards.recognise("https://example.test/careers") == ("careers", "")

    def test_a_non_http_url_is_refused(self):
        with pytest.raises(ValueError):
            boards.recognise("javascript:alert(1)")


class TestPreview:
    async def test_preview_reports_samples_without_saving(self, db, monkeypatch):
        jobs = [
            RawJob(
                source="greenhouse",
                source_id="1",
                company="Acme",
                title="Software Intern",
                location="Nairobi",
                apply_url="https://acme.test/1",
            )
        ]
        monkeypatch.setattr(
            boards, "_build_one", lambda *args, **kwargs: _FakeSource(jobs)
        )

        result = await boards.preview(URL)

        assert result["kind"] == "greenhouse"
        assert result["token"] == "pytestco"
        assert result["found"] == 1
        assert result["samples"][0]["title"] == "Software Intern"
        with Session(engine) as session:
            assert session.exec(select(CustomBoard)).first() is None


class TestAdminApi:
    def test_add_list_and_delete(self, admin_client):
        created = admin_client.post("/api/admin/boards", json={"url": URL, "label": "Acme"})
        assert created.status_code == 200, created.text
        body = created.json()
        assert body["kind"] == "greenhouse"
        assert body["token"] == "pytestco"

        listed = admin_client.get("/api/admin/boards")
        assert URL in [row["url"] for row in listed.json()]

        assert admin_client.delete(f"/api/admin/boards/{body['id']}").status_code == 200
        assert admin_client.get("/api/admin/boards").json() == []

    def test_a_duplicate_is_a_conflict(self, admin_client):
        assert admin_client.post("/api/admin/boards", json={"url": URL}).status_code == 200
        duplicate = admin_client.post("/api/admin/boards", json={"url": URL})
        assert duplicate.status_code == 409

    def test_a_non_http_url_is_rejected(self, admin_client):
        response = admin_client.post(
            "/api/admin/boards", json={"url": "ftp://example.test/board"}
        )
        assert response.status_code == 422

    def test_preview_reports_a_broken_site(self, admin_client, monkeypatch):
        async def boom(_url: str) -> dict:
            raise RuntimeError("connection refused")

        monkeypatch.setattr(boards, "preview", boom)
        response = admin_client.post("/api/admin/boards/preview", json={"url": URL})
        assert response.status_code == 502


class TestPolling:
    def test_enabled_boards_are_built_for_their_poll_cycle(self, db, monkeypatch):
        with Session(engine) as session:
            session.add(CustomBoard(url=URL, kind="greenhouse", token="pytestco"))
            session.commit()

        seen: list[tuple[str, str]] = []

        def fake_build(kind, token, label, url, client):
            seen.append((kind, token))
            return _FakeSource([])

        monkeypatch.setattr(boards, "_build_one", fake_build)

        assert len(boards.build_custom_boards(object(), kinds=boards.ATS_KINDS)) == 1
        assert seen == [("greenhouse", "pytestco")]
        # A careers poll must not pick up an ATS board.
        assert boards.build_custom_boards(object(), kinds=(boards.CAREERS_KIND,)) == []
