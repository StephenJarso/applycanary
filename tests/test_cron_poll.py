"""The external-cron fallback poll (``POST /api/cron/poll``).

The scheduler runs inside the web process, so a sleeping host polls nothing.
This route exists to be driven from outside, which makes its auth the whole
point: no token configured means it must refuse, never run open.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.api import router as api_router
from app.main import create_app
from app.pipeline.ingest import IngestSummary


@pytest.fixture
def client() -> TestClient:
    return TestClient(create_app(), follow_redirects=False)


def _settings(token: str) -> SimpleNamespace:
    return SimpleNamespace(cron_token=token)


def test_refuses_when_no_token_is_configured(client, monkeypatch):
    monkeypatch.setattr(api_router, "get_settings", lambda: _settings(""))
    response = client.post("/api/cron/poll")
    assert response.status_code == 503
    assert "CRON_TOKEN" in response.json()["detail"]


def test_rejects_a_wrong_token(client, monkeypatch):
    monkeypatch.setattr(api_router, "get_settings", lambda: _settings("s3cret"))
    response = client.post("/api/cron/poll", params={"token": "guess"})
    assert response.status_code == 401


def test_accepts_the_token_as_a_header(client, monkeypatch):
    monkeypatch.setattr(api_router, "get_settings", lambda: _settings("s3cret"))

    async def fake_poll() -> IngestSummary:
        return IngestSummary(sources_run=2, found=3, new=1)

    monkeypatch.setattr("app.pipeline.ingest.poll_curated", fake_poll)
    monkeypatch.setattr("app.pipeline.ingest.poll_broad", fake_poll)

    async def no_scoring() -> None:
        return None

    monkeypatch.setattr("app.scheduler.job_score_new", no_scoring)

    response = client.post("/api/cron/poll", headers={"X-Cron-Token": "s3cret"})
    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["detail"]["sources"] == 4
    assert body["detail"]["new"] == 2


def test_does_nothing_while_the_scheduler_is_running(client, monkeypatch):
    monkeypatch.setattr(api_router, "get_settings", lambda: _settings("s3cret"))
    monkeypatch.setattr(
        "app.scheduler.get_scheduler",
        lambda: SimpleNamespace(running=True),
    )

    async def must_not_poll() -> IngestSummary:
        raise AssertionError("cron poll must not run alongside the scheduler")

    monkeypatch.setattr("app.pipeline.ingest.poll_curated", must_not_poll)

    response = client.post("/api/cron/poll", params={"token": "s3cret"})
    assert response.status_code == 200
    assert response.json()["detail"]["skipped"] is True
