"""Stale-source detection and the daily alert.

A connector that 404s every cycle, or succeeds while parsing nothing, is the
failure mode that quietly empties the feed. These tests pin the boundary: a day
of silence is an alert, an hour is not.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, select

from app.auth import SESSION_COOKIE_NAME, create_session_token
from app.db import engine, init_db
from app.main import create_app
from app.models import SourceRun, User, utcnow
from app.pipeline.health import StaleSource, alert_body, stale_sources
from app.scheduler import job_source_health

_CREATED: list[int] = []


def _add(source: str, *, ok: bool, found: int, hours_ago: float) -> None:
    with Session(engine) as session:
        run = SourceRun(
            source=source,
            started_at=utcnow() - timedelta(hours=hours_ago),
            duration_ms=5,
            found=found,
            new_jobs=found,
            ok=ok,
            error="" if ok else "HTTP 404",
        )
        session.add(run)
        session.commit()
        session.refresh(run)
        _CREATED.append(run.id or 0)


@pytest.fixture
def db():
    init_db()
    _CREATED.clear()
    yield
    with Session(engine) as session:
        for pk in _CREATED:
            row = session.get(SourceRun, pk)
            if row is not None:
                session.delete(row)
        session.commit()


def _flagged(session: Session, names: set[str]) -> dict[str, StaleSource]:
    return {item.source: item for item in stale_sources(session) if item.source in names}


class TestStaleSources:
    def test_a_source_failing_for_a_day_is_flagged(self, db):
        _add("health-failing", ok=True, found=3, hours_ago=50)
        _add("health-failing", ok=False, found=0, hours_ago=2)

        with Session(engine) as session:
            flagged = _flagged(session, {"health-failing"})

        item = flagged["health-failing"]
        assert item.reason.startswith("failing since")
        assert item.last_ok_at is not None

    def test_a_single_failure_is_not_an_alert(self, db):
        _add("health-recovering", ok=True, found=3, hours_ago=1)
        _add("health-recovering", ok=False, found=0, hours_ago=0.2)

        with Session(engine) as session:
            assert "health-recovering" not in _flagged(session, {"health-recovering"})

    def test_never_succeeding_is_reported_plainly(self, db):
        _add("health-new-token", ok=False, found=0, hours_ago=30)

        with Session(engine) as session:
            item = _flagged(session, {"health-new-token"})["health-new-token"]

        assert "never completed" in item.reason

    def test_a_day_of_empty_runs_is_flagged(self, db):
        _add("health-empty", ok=True, found=0, hours_ago=30)
        _add("health-empty", ok=True, found=0, hours_ago=1)

        with Session(engine) as session:
            item = _flagged(session, {"health-empty"})["health-empty"]

        assert item.reason == "no postings in its last 2 runs"

    def test_a_healthy_source_is_not_flagged(self, db):
        _add("health-healthy", ok=True, found=4, hours_ago=0.1)

        with Session(engine) as session:
            assert "health-healthy" not in _flagged(session, {"health-healthy"})


class TestSourcesEndpoint:
    def test_the_status_page_carries_the_same_verdict(self, db):
        _add("health-endpoint", ok=True, found=2, hours_ago=50)
        _add("health-endpoint", ok=False, found=0, hours_ago=1)

        with Session(engine) as session:
            user = User(
                email="health-viewer@example.test",
                password_hash="x",
                email_verified=True,
                is_active=True,
            )
            session.add(user)
            session.commit()
            session.refresh(user)

        try:
            client = TestClient(create_app(), follow_redirects=False)
            client.cookies.set(SESSION_COOKIE_NAME, create_session_token(user))
            response = client.get("/api/sources")
            assert response.status_code == 200
            row = next(
                item for item in response.json() if item["source"] == "health-endpoint"
            )
            assert row["stale"] is True
            assert "failing" in row["stale_reason"]
        finally:
            with Session(engine) as session:
                stored = session.exec(
                    select(User).where(User.email == "health-viewer@example.test")
                ).first()
                if stored is not None:
                    session.delete(stored)
                    session.commit()


class TestAlert:
    def test_the_body_names_the_source_and_counts(self):
        subject, html, text = alert_body(
            [
                StaleSource(
                    source="careers/hennge",
                    reason="failing since 2026-11-09 04:00 UTC",
                    last_ok_at=None,
                    last_run_at=utcnow(),
                    runs=12,
                )
            ]
        )
        assert "1 source" in subject
        assert "careers/hennge" in html
        assert "careers/hennge" in text


class TestJob:
    async def test_it_emails_the_operator(self, db, monkeypatch):
        sent: list[str] = []

        async def fake_send(subject: str, html: str, text: str, **kwargs: object) -> bool:
            sent.append(subject)
            return True

        monkeypatch.setattr("app.notify.email.send", fake_send)
        monkeypatch.setattr(
            "app.pipeline.health.stale_sources",
            lambda _session: [
                StaleSource(
                    source="careers/quiet",
                    reason="no postings in its last 9 runs",
                    last_ok_at=None,
                    last_run_at=utcnow(),
                    runs=9,
                )
            ],
        )

        await job_source_health()

        assert sent and "1 source" in sent[0]

    async def test_it_stays_quiet_when_everything_produced(self, db, monkeypatch):
        async def must_not_send(*_args: object, **_kwargs: object) -> bool:
            raise AssertionError("no alert expected for healthy sources")

        monkeypatch.setattr("app.notify.email.send", must_not_send)
        monkeypatch.setattr("app.pipeline.health.stale_sources", lambda _session: [])

        await job_source_health()
