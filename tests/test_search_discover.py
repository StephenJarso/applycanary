"""Search expansion + role-driven discovery tests.

Covers the two \"why do I only see one Go role\" fixes:
1. /jobs search is tokenized over title+company+description, so \"go developer\"
   finds \"Senior Golang Engineer\" where a literal substring match found nothing.
2. Explicit searches surface unscored matches, not just postings the scheduler
   has already judged.
Plus the query-building for role-driven discovery (target titles + skills +
GitHub evidence). No network: the discovery network path is exercised by the
live app, not the suite.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, select

from app.auth import hash_password
from app.db import engine, init_db
from app.main import create_app
from app.models import Job, JobStatus, Profile, User
from app.pipeline.discover import build_queries


@pytest.fixture(autouse=True)
def db():
    init_db()
    with Session(engine) as session:
        for row in session.exec(select(Profile)).all():
            session.delete(row)
        for row in session.exec(select(Job)).all():
            session.delete(row)
        for row in session.exec(select(User)).all():
            session.delete(row)
        session.commit()

        user = User(email="go@example.com", password_hash=hash_password("pw"))
        session.add(user)
        session.commit()
        session.refresh(user)
        session.add(Profile(user_id=user.id, skills=["go", "backend"]))
        session.add_all([
            Job(
                fingerprint="fp-go-1", source="test", company="Checkout.com",
                title="Senior Golang Engineer",
                description="Build high-throughput payment systems in Go.",
                status=JobStatus.NEW,
            ),
            Job(
                fingerprint="fp-go-2", source="test", company="GitLab",
                title="Senior Backend Engineer, Analytics (Golang)",
                description="Go services, instrumentation, ClickHouse.",
                status=JobStatus.NEW,
            ),
            Job(
                fingerprint="fp-go-3", source="test", company="Stripe",
                title="Backend Engineer, Developer SDKs (Golang)",
                description="Go, TypeScript, SDK design.",
                status=JobStatus.NEW,
            ),
            Job(
                fingerprint="fp-barista", source="test", company="Coffee Co",
                title="Barista",
                description="Espresso, latte art, customer service.",
                status=JobStatus.NEW,
            ),
        ])
        session.commit()
    yield


def _client() -> TestClient:
    from app.auth import create_session_token

    client = TestClient(create_app(), follow_redirects=False)
    with Session(engine) as session:
        user = session.exec(select(User)).one()
        client.cookies.set("applycanary_session", create_session_token(user))
    return client


def _titles(res: dict) -> list[str]:
    return [j["title"] for j in res["jobs"]]


def test_search_go_developer_finds_golang_roles():
    """Tokenized search: 'go developer' matches titles containing golang."""
    client = _client()
    res = client.get("/api/jobs", params={"q": "go developer", "sort": "newest", "limit": 100})
    assert res.status_code == 200
    titles = _titles(res.json())
    assert any("Golang" in t for t in titles)
    assert "Barista" not in titles
    # All three Go postings match, not just one.
    assert len(titles) >= 3


def test_search_finds_unscored_matches():
    """An explicit search surfaces matches the scheduler has not scored yet."""
    client = _client()
    # Default view hides unscored postings (no JobScore rows exist here).
    res = client.get("/api/jobs", params={"sort": "newest", "limit": 100})
    assert res.status_code == 200
    assert len(res.json()["jobs"]) == 0

    res = client.get("/api/jobs", params={"q": "golang", "sort": "newest", "limit": 100})
    assert res.status_code == 200
    assert any("Golang" in t for t in _titles(res.json()))


def test_search_matches_description_terms():
    client = _client()
    res = client.get("/api/jobs", params={"q": "latte art"})
    assert res.status_code == 200
    assert "Barista" in _titles(res.json())


def test_public_search_is_tokenized_too():
    client = TestClient(create_app(), follow_redirects=False)
    res = client.get("/api/public/jobs", params={"q": "golang backend"})
    assert res.status_code == 200
    assert any("Golang" in j["title"] for j in res.json()["jobs"])


def test_discover_requires_profile_terms():
    with Session(engine) as session:
        profile = session.exec(select(Profile)).one()
        profile.target_titles = []
        profile.skills = []
        profile.github_evidence = {}
        session.add(profile)
        session.commit()

    client = _client()
    res = client.post("/api/actions/discover")
    assert res.status_code == 200
    body = res.json()
    assert body["ok"] is False
    assert "target titles or skills" in body["message"]


def test_build_queries_from_profile():
    profile = Profile(
        target_titles=["Golang Developer", "Backend Engineer"],
        skills=["go", "docker", "python"],
        github_evidence={"skills": ["kubernetes", "grpc"]},
    )
    queries = build_queries(profile)
    # Target titles first, highest signal; skills+Github folded into one phrase.
    assert queries[:2] == ["Golang Developer", "Backend Engineer"]
    assert len(queries) == 3
    assert queries[2].startswith("go docker python kubernetes")


def test_build_queries_empty_profile():
    profile = Profile(target_titles=[], skills=[], github_evidence={})
    assert build_queries(profile) == []
